from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
import threading
from collections.abc import Awaitable, Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import UnionType
from typing import (
    Annotated,
    Any,
    ParamSpec,
    TypeAlias,
    TypeVar,
    Union,
    get_args,
    get_origin,
    get_type_hints,
    overload,
)


T = TypeVar("T")
P = ParamSpec("P")
R = TypeVar("R")
Token: TypeAlias = Hashable | type[Any]
ProviderCallable: TypeAlias = Callable[..., Any]
TypedProvider: TypeAlias = type[T] | Callable[..., T]
DependencyMap: TypeAlias = Mapping[str, Token]

DEFAULT_PRIORITY = 100
NONE_TYPE = type(None)


class _Missing:
    """Kullanıcının None vermesi ile hiç değer vermemesi ayrımını korur."""

    __slots__ = ()

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "MISSING"


MISSING = _Missing()


class DependencyError(Exception):
    """Dependency container için temel hata sınıfı."""


class DependencyNotFoundError(DependencyError):
    """İstenen token için kayıtlı provider bulunamadığında fırlatılır."""


class DependencyResolutionError(DependencyError):
    """Bir dependency güvenli ve deterministik şekilde üretilemediğinde fırlatılır."""


class CircularDependencyError(DependencyResolutionError):
    """A -> B -> A gibi döngüsel bağımlılıklar yakalandığında fırlatılır."""


class AsyncDependencyError(DependencyResolutionError):
    """Async provider senkron API üzerinden çözülmeye çalışıldığında fırlatılır."""


class Lifetime(StrEnum):
    """Provider yaşam döngüsü."""

    TRANSIENT = "transient"
    """Her resolve çağrısında yeni nesne üretir."""

    SINGLETON = "singleton"
    """İlk resolve çağrısında üretir, sonra aynı nesneyi döndürür."""

    SCOPED = "scoped"
    """Aynı scope içinde aynı nesneyi, farklı scope içinde yeni nesneyi döndürür."""


@dataclass(frozen=True, slots=True)
class Inject:
    """Bir parametrenin container tarafından doldurulacağını açıkça belirtir.

    Çoğu durumda bunu yazman gerekmez. Constructor veya fonksiyondaki typehint
    container'a kayıtlıysa dependency otomatik enjekte edilir.

    Ne zaman kullanılır?
        - Parametrenin default değeri var ama yine de container'dan gelsin.
        - Typehint yerine farklı bir token ile çözümleme yapılsın.
        - Dependency kayıtlı değilse hata yerine None gelsin.

    Örnek:
        def __init__(self, camera: Camera = Inject()): ...
        def __init__(self, vehicle=Inject(BaseVehicleCommunication)): ...
        def __init__(self, cache=Inject(CacheService, optional=True)): ...
    """

    token: Any = MISSING
    optional: bool = False


@dataclass(slots=True)
class _Provider:
    token: Token
    factory: Any
    lifetime: Lifetime
    dependencies: dict[str, Token] = field(default_factory=dict)
    priority: int = DEFAULT_PRIORITY
    instance: Any = MISSING
    singleton: Any = MISSING
    sync_lock: threading.RLock = field(default_factory=threading.RLock)
    async_lock: asyncio.Lock | None = None

    @property
    def has_instance(self) -> bool:
        return self.instance is not MISSING

    def get_async_lock(self) -> asyncio.Lock:
        if self.async_lock is None:
            self.async_lock = asyncio.Lock()
        return self.async_lock


@dataclass(frozen=True, slots=True)
class _Registration:
    token: Token
    factory: Any
    lifetime: Lifetime
    dependencies: dict[str, Token]
    priority: int
    instance: Any = MISSING


@dataclass(frozen=True, slots=True)
class _InjectCandidate:
    name: str
    token: Token
    explicit: bool
    optional: bool = False


@dataclass(frozen=True, slots=True)
class _InjectionPlan:
    signature: inspect.Signature
    candidates: tuple[_InjectCandidate, ...]


@dataclass(frozen=True, slots=True)
class _AnnotationToken:
    token: Token
    optional: bool = False


_default_container: DependencyContainer | None = None
_current_container: contextvars.ContextVar[DependencyContainer | None] = (
    contextvars.ContextVar("current_dependency_container", default=None)
)
_resolution_stack: contextvars.ContextVar[tuple[Token, ...]] = contextvars.ContextVar(
    "dependency_resolution_stack",
    default=(),
)


class DependencyContainer:
    """Senkron ve asenkron uyumlu küçük dependency injection container.

    Bu sınıf uygulamanın servis haritasını tutar. Kullanıcı abstract bir token
    kaydeder, container o token istendiğinde hangi concrete sınıfın veya hangi
    factory fonksiyonunun çalışacağını bilir.

    Temel örnek:
        container = DependencyContainer()
        container.singleton(abstract=BaseCamera, concrete=OpenCVCamera)
        camera = container.resolve(BaseCamera)

    Kısa pozisyonel kullanım:
        container.singleton(BaseCamera, OpenCVCamera)

    Concrete sınıfı kendi token'ı ile kaydetme:
        container.transient(concrete=SafeMission)
    """

    __slots__ = (
        "auto_wire",
        "parent",
        "_context_tokens",
        "_providers",
        "_scope_cache",
    )

    def __init__(
        self,
        *,
        parent: DependencyContainer | None = None,
        auto_wire: bool = True,
    ) -> None:
        self.parent = parent
        self.auto_wire = auto_wire
        self._providers: dict[Token, _Provider] = {}
        self._scope_cache: dict[Token, Any] = {}
        self._context_tokens: list[contextvars.Token[DependencyContainer | None]] = []

    def __contains__(self, token: Token) -> bool:
        return self.has(token)

    def __enter__(self) -> DependencyContainer:
        self._context_tokens.append(_current_container.set(self))
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.shutdown()
        self._reset_current_container()

    async def __aenter__(self) -> DependencyContainer:
        self._context_tokens.append(_current_container.set(self))
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.shutdown_async()
        self._reset_current_container()

    @overload
    def register(
        self,
        token: type[T],
        provider: TypedProvider[T] = ...,
        *,
        lifetime: Lifetime | str = ...,
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def register(
        self,
        token: type[T],
        *,
        factory: Callable[..., T],
        lifetime: Lifetime | str = ...,
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def register(
        self,
        token: type[T],
        *,
        instance: T,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def register(
        self,
        *,
        abstract: type[T],
        concrete: TypedProvider[T],
        lifetime: Lifetime | str = ...,
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def register(
        self,
        *,
        abstract: type[T],
        factory: Callable[..., T],
        lifetime: Lifetime | str = ...,
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def register(
        self,
        *,
        abstract: type[T],
        instance: T,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def register(
        self,
        *,
        concrete: TypedProvider[T],
        lifetime: Lifetime | str = ...,
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    def register(
        self,
        token: Token | None = None,
        provider: Any = MISSING,
        *,
        abstract: Token | None = None,
        concrete: Any = MISSING,
        factory: Any = MISSING,
        instance: Any = MISSING,
        lifetime: Lifetime | str = Lifetime.TRANSIENT,
        dependencies: DependencyMap | None = None,
        priority: int = DEFAULT_PRIORITY,
    ) -> DependencyContainer:
        """Yeni dependency kaydı ekler.

        Parametreler deterministik olsun diye iki kullanım biçimi desteklenir:

        1. Pozisyonel kısa kullanım:
            register(BaseService, RealService)

        2. İsimli ve okunaklı kullanım:
            register(abstract=BaseService, concrete=RealService)

        Parametreler:
            token: Çözülecek anahtar. abstract ile aynı anlama gelir.
            provider: Üretici. concrete ile aynı anlama gelir.
            abstract: Token için okunaklı isimli parametre.
            concrete: Concrete sınıf veya senkron/asenkron callable.
            factory: Provider yerine açıkça factory fonksiyonu vermek için.
            instance: Hazır nesne kaydetmek için. Yaşam döngüsü singleton olur.
            lifetime: transient, singleton veya scoped.
            dependencies: Factory/constructor parametre adı -> token eşlemesi.
            priority: Warmup sırasında düşük sayı önce çözülür.
        """

        registration = self._normalize_registration(
            token=token,
            provider=provider,
            abstract=abstract,
            concrete=concrete,
            factory=factory,
            instance=instance,
            lifetime=lifetime,
            dependencies=dependencies,
            priority=priority,
        )

        self._providers[registration.token] = _Provider(
            token=registration.token,
            factory=registration.factory,
            lifetime=registration.lifetime,
            dependencies=registration.dependencies,
            priority=registration.priority,
            instance=registration.instance,
        )
        return self

    @overload
    def singleton(
        self,
        token: type[T],
        provider: TypedProvider[T] = ...,
        *,
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def singleton(
        self,
        token: type[T],
        *,
        factory: Callable[..., T],
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def singleton(
        self,
        token: type[T],
        *,
        instance: T,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def singleton(
        self,
        *,
        abstract: type[T],
        concrete: TypedProvider[T],
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def singleton(
        self,
        *,
        abstract: type[T],
        factory: Callable[..., T],
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def singleton(
        self,
        *,
        abstract: type[T],
        instance: T,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def singleton(
        self,
        *,
        concrete: TypedProvider[T],
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    def singleton(
        self,
        token: Token | None = None,
        provider: Any = MISSING,
        *,
        abstract: Token | None = None,
        concrete: Any = MISSING,
        factory: Any = MISSING,
        instance: Any = MISSING,
        dependencies: DependencyMap | None = None,
        priority: int = DEFAULT_PRIORITY,
    ) -> DependencyContainer:
        """Provider'ı singleton olarak kaydeder."""

        return self.register(
            token,
            provider,
            abstract=abstract,
            concrete=concrete,
            factory=factory,
            instance=instance,
            lifetime=Lifetime.SINGLETON,
            dependencies=dependencies,
            priority=priority,
        )

    @overload
    def transient(
        self,
        token: type[T],
        provider: TypedProvider[T] = ...,
        *,
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def transient(
        self,
        token: type[T],
        *,
        factory: Callable[..., T],
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def transient(
        self,
        *,
        abstract: type[T],
        concrete: TypedProvider[T],
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def transient(
        self,
        *,
        abstract: type[T],
        factory: Callable[..., T],
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def transient(
        self,
        *,
        concrete: TypedProvider[T],
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    def transient(
        self,
        token: Token | None = None,
        provider: Any = MISSING,
        *,
        abstract: Token | None = None,
        concrete: Any = MISSING,
        factory: Any = MISSING,
        dependencies: DependencyMap | None = None,
        priority: int = DEFAULT_PRIORITY,
    ) -> DependencyContainer:
        """Provider'ı transient olarak kaydeder."""

        return self.register(
            token,
            provider,
            abstract=abstract,
            concrete=concrete,
            factory=factory,
            lifetime=Lifetime.TRANSIENT,
            dependencies=dependencies,
            priority=priority,
        )

    @overload
    def scoped(
        self,
        token: type[T],
        provider: TypedProvider[T] = ...,
        *,
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def scoped(
        self,
        token: type[T],
        *,
        factory: Callable[..., T],
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def scoped(
        self,
        *,
        abstract: type[T],
        concrete: TypedProvider[T],
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def scoped(
        self,
        *,
        abstract: type[T],
        factory: Callable[..., T],
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def scoped(
        self,
        *,
        concrete: TypedProvider[T],
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    def scoped(
        self,
        token: Token | None = None,
        provider: Any = MISSING,
        *,
        abstract: Token | None = None,
        concrete: Any = MISSING,
        factory: Any = MISSING,
        dependencies: DependencyMap | None = None,
        priority: int = DEFAULT_PRIORITY,
    ) -> DependencyContainer:
        """Provider'ı scope yaşam döngüsü ile kaydeder."""

        return self.register(
            token,
            provider,
            abstract=abstract,
            concrete=concrete,
            factory=factory,
            lifetime=Lifetime.SCOPED,
            dependencies=dependencies,
            priority=priority,
        )

    @overload
    def instance(
        self,
        token: type[T],
        instance: T,
        *,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def instance(
        self,
        *,
        abstract: type[T],
        instance: T,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def instance(
        self,
        *,
        instance: T,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    def instance(
        self,
        token: Token | None = None,
        instance: Any = MISSING,
        *,
        abstract: Token | None = None,
        priority: int = DEFAULT_PRIORITY,
    ) -> DependencyContainer:
        """Hazır nesneyi singleton olarak kaydeder."""

        return self.register(
            token,
            abstract=abstract,
            instance=instance,
            lifetime=Lifetime.SINGLETON,
            priority=priority,
        )

    @overload
    def provider(
        self,
        token: type[T],
        *,
        lifetime: Lifetime | str = ...,
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> Callable[[Callable[..., T]], Callable[..., T]]:
        ...

    @overload
    def provider(
        self,
        *,
        abstract: type[T],
        lifetime: Lifetime | str = ...,
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> Callable[[Callable[..., T]], Callable[..., T]]:
        ...

    @overload
    def provider(
        self,
        token: None = ...,
        *,
        lifetime: Lifetime | str = ...,
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> Callable[[Callable[..., T]], Callable[..., T]]:
        ...

    def provider(
        self,
        token: Token | None = None,
        *,
        abstract: Token | None = None,
        lifetime: Lifetime | str = Lifetime.TRANSIENT,
        dependencies: DependencyMap | None = None,
        priority: int = DEFAULT_PRIORITY,
    ) -> Callable[[ProviderCallable | type[Any]], ProviderCallable | type[Any]]:
        """Factory fonksiyonunu decorator ile kaydetmek için kullanılır."""

        def decorator(factory: ProviderCallable | type[Any]) -> ProviderCallable | type[Any]:
            self.register(
                token,
                abstract=abstract,
                factory=factory,
                lifetime=lifetime,
                dependencies=dependencies,
                priority=priority,
            )
            return factory

        return decorator

    @overload
    def inject(
        self,
        target: type[T],
        *,
        dependencies: DependencyMap | None = ...,
        strict: bool = ...,
        **named_dependencies: Token,
    ) -> type[T]:
        ...

    @overload
    def inject(
        self,
        target: Callable[P, R],
        *,
        dependencies: DependencyMap | None = ...,
        strict: bool = ...,
        **named_dependencies: Token,
    ) -> Callable[P, R]:
        ...

    @overload
    def inject(
        self,
        target: None = ...,
        *,
        dependencies: DependencyMap | None = ...,
        strict: bool = ...,
        **named_dependencies: Token,
    ) -> Callable[[T], T]:
        ...

    def inject(
        self,
        target: Callable[..., T] | type[T] | None = None,
        *,
        dependencies: DependencyMap | None = None,
        strict: bool = False,
        **named_dependencies: Token,
    ) -> Callable[..., T] | type[T] | Callable[[Callable[..., T] | type[T]], Any]:
        """Bu container'a bağlı injection decorator'ı üretir."""

        return injection(
            target,
            container=self,
            dependencies=dependencies,
            strict=strict,
            **named_dependencies,
        )

    def unregister(self, token: Token) -> None:
        """Token kaydını ve scope cache'ini siler."""

        self._providers.pop(token, None)
        self._scope_cache.pop(token, None)

    def has(self, token: Token) -> bool:
        """Token açıkça kayıtlı mı diye bakar. Auto-wire bu kontrolde sayılmaz."""

        return self._find_provider(token) is not None

    def can_resolve(self, token: Token) -> bool:
        """Token kayıtlıysa veya auto-wire ile üretilebiliyorsa True döner."""

        return self.has(token) or (self.auto_wire and can_autowire(token))

    def registered_tokens(self) -> tuple[Token, ...]:
        """Bu container'da açıkça kayıtlı token'ları deterministik sırada döner."""

        return tuple(sorted(self._providers, key=format_token))

    def create_scope(self) -> DependencyContainer:
        """Bu container'ı parent kabul eden yeni scope container'ı oluşturur."""

        return DependencyContainer(parent=self, auto_wire=self.auto_wire)

    @overload
    def resolve(self, token: type[T]) -> T:
        ...

    @overload
    def resolve(self, token: Token) -> Any:
        ...

    def resolve(self, token: Token) -> Any:
        """Token karşılığındaki dependency'yi senkron olarak çözer."""

        provider = self._get_or_autowire_provider(token)
        return self._resolve_provider(provider)

    @overload
    async def resolve_async(self, token: type[T]) -> T:
        ...

    @overload
    async def resolve_async(self, token: Token) -> Any:
        ...

    async def resolve_async(self, token: Token) -> Any:
        """Token karşılığındaki dependency'yi asenkron olarak çözer."""

        provider = self._get_or_autowire_provider(token)
        return await self._resolve_provider_async(provider)

    def build(
        self,
        factory: ProviderCallable | type[T],
        *,
        dependencies: DependencyMap | None = None,
    ) -> T:
        """Kaydetmeden, sadece verilen factory/sınıf için dependency enjekte eder."""

        return self._call_factory(factory, normalize_dependencies(dependencies))

    async def build_async(
        self,
        factory: ProviderCallable | type[T],
        *,
        dependencies: DependencyMap | None = None,
    ) -> T:
        """build metodunun asenkron provider destekleyen halidir."""

        return await self._call_factory_async(factory, normalize_dependencies(dependencies))

    def warmup(
        self,
        tokens: Iterable[Token] | None = None,
        *,
        lifetimes: Iterable[Lifetime | str] = (Lifetime.SINGLETON,),
    ) -> None:
        """Seçilen provider'ları priority sırasıyla önceden üretir."""

        for provider in self._warmup_providers(tokens, lifetimes):
            self.resolve(provider.token)

    async def warmup_async(
        self,
        tokens: Iterable[Token] | None = None,
        *,
        lifetimes: Iterable[Lifetime | str] = (Lifetime.SINGLETON,),
    ) -> None:
        """warmup metodunun asenkron provider destekleyen halidir."""

        for provider in self._warmup_providers(tokens, lifetimes):
            await self.resolve_async(provider.token)

    def shutdown(self) -> None:
        """Scope cache ve root singleton nesnelerini senkron şekilde kapatır."""

        self._dispose_many(self._scope_cache.values())
        self._scope_cache.clear()

        if self.parent is None:
            for provider in self._providers.values():
                for instance in cached_instances(provider):
                    self._dispose(instance)
                provider.singleton = MISSING

    async def shutdown_async(self) -> None:
        """Scope cache ve root singleton nesnelerini asenkron şekilde kapatır."""

        await self._dispose_many_async(self._scope_cache.values())
        self._scope_cache.clear()

        if self.parent is None:
            for provider in self._providers.values():
                for instance in cached_instances(provider):
                    await self._dispose_async(instance)
                provider.singleton = MISSING

    def _normalize_registration(
        self,
        *,
        token: Token | None,
        provider: Any,
        abstract: Token | None,
        concrete: Any,
        factory: Any,
        instance: Any,
        lifetime: Lifetime | str,
        dependencies: DependencyMap | None,
        priority: int,
    ) -> _Registration:
        selected_token = choose_token(token=token, abstract=abstract)
        selected_factory = choose_factory(
            provider=provider,
            concrete=concrete,
            factory=factory,
            instance=instance,
        )
        selected_lifetime = Lifetime(lifetime)
        selected_instance = MISSING

        if selected_factory is MISSING:
            if selected_token is not None and inspect.isclass(selected_token):
                selected_factory = selected_token
            else:
                raise DependencyResolutionError(
                    "Dependency kaydı için token/abstract veya provider/concrete "
                    "belirtilmeli."
                )

        if selected_token is None:
            selected_token = infer_token_from_factory(selected_factory)

        if selected_token is None:
            raise DependencyResolutionError(
                "Token belirlenemedi. abstract=... veya token parametresi ver."
            )

        if instance is not MISSING:
            selected_instance = instance
            selected_lifetime = Lifetime.SINGLETON
        elif selected_factory is not MISSING and is_direct_instance(selected_factory):
            selected_instance = selected_factory
            selected_lifetime = Lifetime.SINGLETON

        normalized_dependencies = normalize_dependencies(dependencies)
        normalized_priority = normalize_priority(priority)
        ensure_hashable_token(selected_token)

        return _Registration(
            token=selected_token,
            factory=selected_factory,
            lifetime=selected_lifetime,
            dependencies=normalized_dependencies,
            priority=normalized_priority,
            instance=selected_instance,
        )

    def _reset_current_container(self) -> None:
        if self._context_tokens:
            _current_container.reset(self._context_tokens.pop())

    def _find_provider(self, token: Token) -> _Provider | None:
        if token in self._providers:
            return self._providers[token]
        if self.parent is not None:
            return self.parent._find_provider(token)
        return None

    def _get_or_autowire_provider(self, token: Token) -> _Provider:
        provider = self._find_provider(token)
        if provider is not None:
            return provider

        if self.auto_wire and can_autowire(token):
            return _Provider(
                token=token,
                factory=token,
                lifetime=Lifetime.TRANSIENT,
            )

        raise DependencyNotFoundError(
            f"{format_token(token)} için kayıtlı dependency bulunamadı."
        )

    def _warmup_providers(
        self,
        tokens: Iterable[Token] | None,
        lifetimes: Iterable[Lifetime | str],
    ) -> list[_Provider]:
        selected_lifetimes = {Lifetime(lifetime) for lifetime in lifetimes}
        selected_tokens = set(tokens) if tokens is not None else None

        providers = [
            provider
            for provider in self._providers.values()
            if provider.lifetime in selected_lifetimes
            and (selected_tokens is None or provider.token in selected_tokens)
        ]
        return sorted(providers, key=lambda item: (item.priority, format_token(item.token)))

    def _resolve_provider(self, provider: _Provider) -> Any:
        if provider.has_instance:
            return provider.instance

        stack_token = enter_resolution(provider.token)
        try:
            if provider.lifetime == Lifetime.TRANSIENT:
                return self._call_factory(provider.factory, provider.dependencies)

            if provider.lifetime == Lifetime.SCOPED:
                if provider.token not in self._scope_cache:
                    self._scope_cache[provider.token] = self._call_factory(
                        provider.factory,
                        provider.dependencies,
                    )
                return self._scope_cache[provider.token]

            with provider.sync_lock:
                if provider.singleton is MISSING:
                    provider.singleton = self._call_factory(
                        provider.factory,
                        provider.dependencies,
                    )
                return provider.singleton
        finally:
            exit_resolution(stack_token)

    async def _resolve_provider_async(self, provider: _Provider) -> Any:
        if provider.has_instance:
            return provider.instance

        stack_token = enter_resolution(provider.token)
        try:
            if provider.lifetime == Lifetime.TRANSIENT:
                return await self._call_factory_async(provider.factory, provider.dependencies)

            if provider.lifetime == Lifetime.SCOPED:
                if provider.token not in self._scope_cache:
                    self._scope_cache[provider.token] = await self._call_factory_async(
                        provider.factory,
                        provider.dependencies,
                    )
                return self._scope_cache[provider.token]

            async with provider.get_async_lock():
                if provider.singleton is MISSING:
                    provider.singleton = await self._call_factory_async(
                        provider.factory,
                        provider.dependencies,
                    )
                return provider.singleton
        finally:
            exit_resolution(stack_token)

    def _call_factory(
        self,
        factory: ProviderCallable | type[T],
        dependencies: dict[str, Token],
    ) -> T:
        if inspect.iscoroutinefunction(factory):
            raise AsyncDependencyError(
                f"{format_token(factory)} async çalışıyor; resolve_async/build_async kullan."
            )

        kwargs = resolve_missing_kwargs(
            factory,
            args=(),
            kwargs={},
            dependencies=dependencies,
            container=self,
            strict=True,
        )
        result = factory(**kwargs)

        if inspect.isawaitable(result):
            close_awaitable(result)
            raise AsyncDependencyError(
                f"{format_token(factory)} awaitable döndürdü; "
                "resolve_async/build_async kullan."
            )
        return result

    async def _call_factory_async(
        self,
        factory: ProviderCallable | type[T],
        dependencies: dict[str, Token],
    ) -> T:
        kwargs = await resolve_missing_kwargs_async(
            factory,
            args=(),
            kwargs={},
            dependencies=dependencies,
            container=self,
            strict=True,
        )
        result = factory(**kwargs)

        if inspect.isawaitable(result):
            return await result
        return result

    def _dispose_many(self, instances: Iterable[Any]) -> None:
        seen: set[int] = set()
        for instance in instances:
            if instance is MISSING or id(instance) in seen:
                continue
            seen.add(id(instance))
            self._dispose(instance)

    async def _dispose_many_async(self, instances: Iterable[Any]) -> None:
        seen: set[int] = set()
        for instance in instances:
            if instance is MISSING or id(instance) in seen:
                continue
            seen.add(id(instance))
            await self._dispose_async(instance)

    def _dispose(self, instance: Any) -> None:
        close = getattr(instance, "close", None)
        if not callable(close):
            return

        result = close()
        if inspect.isawaitable(result):
            close_awaitable(result)
            raise AsyncDependencyError(
                f"{format_token(type(instance))}.close() async; shutdown_async kullan."
            )

    async def _dispose_async(self, instance: Any) -> None:
        aclose = getattr(instance, "aclose", None)
        if callable(aclose):
            await maybe_await(aclose())
            return

        close = getattr(instance, "close", None)
        if callable(close):
            await maybe_await(close())


class BaseDependencyContainer:
    """Uygulamaya özel container sınıfları için temel sınıf.

    Projede genelde doğrudan DependencyContainer üretmek yerine bu sınıftan
    kalıtım alan bir AppContainer yazılır ve bütün servis kayıtları configure
    metodunda toplanır.
    """

    __slots__ = ("container",)

    def __init__(
        self,
        *,
        container: DependencyContainer | None = None,
        parent: DependencyContainer | None = None,
        set_as_default: bool = True,
        auto_wire: bool = True,
    ) -> None:
        self.container = container or DependencyContainer(
            parent=parent,
            auto_wire=auto_wire,
        )
        self.configure()

        if set_as_default:
            set_default_container(self.container)

    def configure(self) -> None:
        """Alt sınıf bu metodu override edip servis kayıtlarını burada yapar."""

    @overload
    def resolve(self, token: type[T]) -> T:
        ...

    @overload
    def resolve(self, token: Token) -> Any:
        ...

    def resolve(self, token: Token) -> Any:
        return self.container.resolve(token)

    @overload
    async def resolve_async(self, token: type[T]) -> T:
        ...

    @overload
    async def resolve_async(self, token: Token) -> Any:
        ...

    async def resolve_async(self, token: Token) -> Any:
        return await self.container.resolve_async(token)

    def warmup(
        self,
        tokens: Iterable[Token] | None = None,
        *,
        lifetimes: Iterable[Lifetime | str] = (Lifetime.SINGLETON,),
    ) -> None:
        self.container.warmup(tokens=tokens, lifetimes=lifetimes)

    async def warmup_async(
        self,
        tokens: Iterable[Token] | None = None,
        *,
        lifetimes: Iterable[Lifetime | str] = (Lifetime.SINGLETON,),
    ) -> None:
        await self.container.warmup_async(tokens=tokens, lifetimes=lifetimes)

    @overload
    def singleton(
        self,
        token: type[T],
        provider: TypedProvider[T] = ...,
        *,
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def singleton(
        self,
        token: type[T],
        *,
        factory: Callable[..., T],
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def singleton(
        self,
        token: type[T],
        *,
        instance: T,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def singleton(
        self,
        *,
        abstract: type[T],
        concrete: TypedProvider[T],
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def singleton(
        self,
        *,
        abstract: type[T],
        factory: Callable[..., T],
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def singleton(
        self,
        *,
        abstract: type[T],
        instance: T,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def singleton(
        self,
        *,
        concrete: TypedProvider[T],
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    def singleton(
        self,
        token: Token | None = None,
        provider: Any = MISSING,
        *,
        abstract: Token | None = None,
        concrete: Any = MISSING,
        factory: Any = MISSING,
        instance: Any = MISSING,
        dependencies: DependencyMap | None = None,
        priority: int = DEFAULT_PRIORITY,
    ) -> DependencyContainer:
        return self.container.singleton(
            token,
            provider,
            abstract=abstract,
            concrete=concrete,
            factory=factory,
            instance=instance,
            dependencies=dependencies,
            priority=priority,
        )

    @overload
    def transient(
        self,
        token: type[T],
        provider: TypedProvider[T] = ...,
        *,
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def transient(
        self,
        token: type[T],
        *,
        factory: Callable[..., T],
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def transient(
        self,
        *,
        abstract: type[T],
        concrete: TypedProvider[T],
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def transient(
        self,
        *,
        abstract: type[T],
        factory: Callable[..., T],
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def transient(
        self,
        *,
        concrete: TypedProvider[T],
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    def transient(
        self,
        token: Token | None = None,
        provider: Any = MISSING,
        *,
        abstract: Token | None = None,
        concrete: Any = MISSING,
        factory: Any = MISSING,
        dependencies: DependencyMap | None = None,
        priority: int = DEFAULT_PRIORITY,
    ) -> DependencyContainer:
        return self.container.transient(
            token,
            provider,
            abstract=abstract,
            concrete=concrete,
            factory=factory,
            dependencies=dependencies,
            priority=priority,
        )

    @overload
    def scoped(
        self,
        token: type[T],
        provider: TypedProvider[T] = ...,
        *,
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def scoped(
        self,
        token: type[T],
        *,
        factory: Callable[..., T],
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def scoped(
        self,
        *,
        abstract: type[T],
        concrete: TypedProvider[T],
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def scoped(
        self,
        *,
        abstract: type[T],
        factory: Callable[..., T],
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def scoped(
        self,
        *,
        concrete: TypedProvider[T],
        dependencies: DependencyMap | None = ...,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    def scoped(
        self,
        token: Token | None = None,
        provider: Any = MISSING,
        *,
        abstract: Token | None = None,
        concrete: Any = MISSING,
        factory: Any = MISSING,
        dependencies: DependencyMap | None = None,
        priority: int = DEFAULT_PRIORITY,
    ) -> DependencyContainer:
        return self.container.scoped(
            token,
            provider,
            abstract=abstract,
            concrete=concrete,
            factory=factory,
            dependencies=dependencies,
            priority=priority,
        )

    @overload
    def instance(
        self,
        token: type[T],
        instance: T,
        *,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def instance(
        self,
        *,
        abstract: type[T],
        instance: T,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    @overload
    def instance(
        self,
        *,
        instance: T,
        priority: int = ...,
    ) -> DependencyContainer:
        ...

    def instance(
        self,
        token: Token | None = None,
        instance: Any = MISSING,
        *,
        abstract: Token | None = None,
        priority: int = DEFAULT_PRIORITY,
    ) -> DependencyContainer:
        return self.container.instance(
            token,
            instance,
            abstract=abstract,
            priority=priority,
        )

    @overload
    def inject(
        self,
        target: type[T],
        *,
        dependencies: DependencyMap | None = ...,
        strict: bool = ...,
        **named_dependencies: Token,
    ) -> type[T]:
        ...

    @overload
    def inject(
        self,
        target: Callable[P, R],
        *,
        dependencies: DependencyMap | None = ...,
        strict: bool = ...,
        **named_dependencies: Token,
    ) -> Callable[P, R]:
        ...

    @overload
    def inject(
        self,
        target: None = ...,
        *,
        dependencies: DependencyMap | None = ...,
        strict: bool = ...,
        **named_dependencies: Token,
    ) -> Callable[[T], T]:
        ...

    def inject(
        self,
        target: Callable[..., T] | type[T] | None = None,
        *,
        dependencies: DependencyMap | None = None,
        strict: bool = False,
        **named_dependencies: Token,
    ) -> Callable[..., T] | type[T] | Callable[[Callable[..., T] | type[T]], Any]:
        return self.container.inject(
            target,
            dependencies=dependencies,
            strict=strict,
            **named_dependencies,
        )

    def create_scope(self) -> DependencyContainer:
        return self.container.create_scope()

    def shutdown(self) -> None:
        self.container.shutdown()

    async def shutdown_async(self) -> None:
        await self.container.shutdown_async()


def set_default_container(container: DependencyContainer) -> None:
    """Decorator'ların varsayılan olarak kullanacağı container'ı ayarlar."""

    global _default_container
    _default_container = container


def get_default_container() -> DependencyContainer:
    """Varsayılan container'ı döner; yoksa boş bir container oluşturur."""

    global _default_container
    if _default_container is None:
        _default_container = DependencyContainer()
    return _default_container


def get_current_container() -> DependencyContainer:
    """Aktif scope container'ı varsa onu, yoksa default container'ı döner."""

    return _current_container.get() or get_default_container()


@overload
def injection(
    target: type[T],
    *,
    container: DependencyContainer | None = ...,
    dependencies: DependencyMap | None = ...,
    strict: bool = ...,
    **named_dependencies: Token,
) -> type[T]:
    ...


@overload
def injection(
    target: Callable[P, R],
    *,
    container: DependencyContainer | None = ...,
    dependencies: DependencyMap | None = ...,
    strict: bool = ...,
    **named_dependencies: Token,
) -> Callable[P, R]:
    ...


@overload
def injection(
    target: None = ...,
    *,
    container: DependencyContainer | None = ...,
    dependencies: DependencyMap | None = ...,
    strict: bool = ...,
    **named_dependencies: Token,
) -> Callable[[T], T]:
    ...


def injection(
    target: Callable[..., T] | type[T] | None = None,
    *,
    container: DependencyContainer | None = None,
    dependencies: DependencyMap | None = None,
    strict: bool = False,
    **named_dependencies: Token,
) -> Callable[..., T] | type[T] | Callable[[Callable[..., T] | type[T]], Any]:
    """Sınıf veya fonksiyon parametrelerine otomatik dependency enjekte eder.

    Kullanım:
        @injection()
        class SafeMission:
            def __init__(self, vehicle: BaseVehicleCommunication): ...

    Kurallar:
        - Parametre zaten kullanıcı tarafından verilmişse dokunulmaz.
        - Typehint container'da kayıtlıysa otomatik enjekte edilir.
        - Inject(...) yazılmışsa explicit kabul edilir ve çözülmeye çalışılır.
        - strict=True ise kayıtlı olmayan implicit typehint hata üretir.
    """

    dependency_map = merge_dependencies(dependencies, named_dependencies)

    def decorator(obj: Callable[..., T] | type[T]) -> Callable[..., T] | type[T]:
        if inspect.isclass(obj):
            return decorate_class(
                obj,
                container=container,
                dependencies=dependency_map,
                strict=strict,
            )
        if callable(obj):
            return decorate_callable(
                obj,
                container=container,
                dependencies=dependency_map,
                strict=strict,
            )
        raise TypeError("@injection yalnızca sınıf veya callable üzerine uygulanabilir.")

    if target is None:
        return decorator
    return decorator(target)


def decorate_class(
    cls: type[T],
    *,
    container: DependencyContainer | None,
    dependencies: dict[str, Token],
    strict: bool,
) -> type[T]:
    """Sınıfın __init__ metodunu injection destekli hale getirir."""

    original_init = getattr(cls.__init__, "__dependency_original__", cls.__init__)
    if original_init is object.__init__:
        return cls

    @functools.wraps(original_init)
    def wrapped_init(self: Any, *args: Any, **kwargs: Any) -> None:
        resolver = container or get_current_container()
        injected_kwargs = resolve_missing_kwargs(
            original_init,
            args=(self, *args),
            kwargs=kwargs,
            dependencies=dependencies,
            container=resolver,
            strict=strict,
        )
        original_init(self, *args, **injected_kwargs)

    wrapped_init.__dependency_original__ = original_init  # type: ignore[attr-defined]
    wrapped_init.__dependency_injected__ = True  # type: ignore[attr-defined]
    cls.__init__ = wrapped_init  # type: ignore[method-assign]
    return cls


def decorate_callable(
    func: Callable[..., T],
    *,
    container: DependencyContainer | None,
    dependencies: dict[str, Token],
    strict: bool,
) -> Callable[..., T]:
    """Fonksiyonu injection destekli hale getirir."""

    original_func = getattr(func, "__dependency_original__", func)

    if inspect.iscoroutinefunction(original_func):

        @functools.wraps(original_func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> T:
            resolver = container or get_current_container()
            injected_kwargs = await resolve_missing_kwargs_async(
                original_func,
                args=args,
                kwargs=kwargs,
                dependencies=dependencies,
                container=resolver,
                strict=strict,
            )
            return await original_func(*args, **injected_kwargs)

        async_wrapper.__dependency_original__ = original_func  # type: ignore[attr-defined]
        async_wrapper.__dependency_injected__ = True  # type: ignore[attr-defined]
        return async_wrapper

    @functools.wraps(original_func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> T:
        resolver = container or get_current_container()
        injected_kwargs = resolve_missing_kwargs(
            original_func,
            args=args,
            kwargs=kwargs,
            dependencies=dependencies,
            container=resolver,
            strict=strict,
        )
        return original_func(*args, **injected_kwargs)

    sync_wrapper.__dependency_original__ = original_func  # type: ignore[attr-defined]
    sync_wrapper.__dependency_injected__ = True  # type: ignore[attr-defined]
    return sync_wrapper


def resolve_missing_kwargs(
    func: Callable[..., Any],
    *,
    args: tuple[Any, ...],
    kwargs: Mapping[str, Any],
    dependencies: dict[str, Token],
    container: DependencyContainer,
    strict: bool,
) -> dict[str, Any]:
    """Eksik parametreleri senkron container çözümüyle tamamlar."""

    plan = get_injection_plan(func, dependency_items(dependencies))
    bound = plan.signature.bind_partial(*args, **kwargs)
    final_kwargs = dict(kwargs)

    for candidate in plan.candidates:
        if candidate.name in bound.arguments:
            continue
        if not should_inject(candidate, container, strict):
            if candidate.optional:
                final_kwargs[candidate.name] = None
            continue

        try:
            final_kwargs[candidate.name] = container.resolve(candidate.token)
        except DependencyNotFoundError:
            if candidate.optional:
                final_kwargs[candidate.name] = None
                continue
            raise

    return final_kwargs


async def resolve_missing_kwargs_async(
    func: Callable[..., Any],
    *,
    args: tuple[Any, ...],
    kwargs: Mapping[str, Any],
    dependencies: dict[str, Token],
    container: DependencyContainer,
    strict: bool,
) -> dict[str, Any]:
    """Eksik parametreleri asenkron container çözümüyle tamamlar."""

    plan = get_injection_plan(func, dependency_items(dependencies))
    bound = plan.signature.bind_partial(*args, **kwargs)
    final_kwargs = dict(kwargs)

    for candidate in plan.candidates:
        if candidate.name in bound.arguments:
            continue
        if not should_inject(candidate, container, strict):
            if candidate.optional:
                final_kwargs[candidate.name] = None
            continue

        try:
            final_kwargs[candidate.name] = await container.resolve_async(candidate.token)
        except DependencyNotFoundError:
            if candidate.optional:
                final_kwargs[candidate.name] = None
                continue
            raise

    return final_kwargs


def should_inject(
    candidate: _InjectCandidate,
    container: DependencyContainer,
    strict: bool,
) -> bool:
    if candidate.explicit:
        if candidate.optional and not container.has(candidate.token):
            return False
        return True
    if container.has(candidate.token):
        return True
    if strict:
        raise DependencyNotFoundError(
            f"{candidate.name} parametresi için {format_token(candidate.token)} kayıtlı değil."
        )
    return False


@functools.lru_cache(maxsize=4096)
def get_injection_plan(
    func: Callable[..., Any],
    dependency_items_: tuple[tuple[str, Token], ...],
) -> _InjectionPlan:
    """Callable imzasını analiz edip cache'lenen injection planı üretir."""

    signature = inspect.signature(func)
    hints = safe_type_hints(func)
    dependencies = dict(dependency_items_)
    candidates: list[_InjectCandidate] = []
    used_dependency_names: set[str] = set()

    for name, parameter in signature.parameters.items():
        if parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue

        candidate = make_inject_candidate(
            name=name,
            parameter=parameter,
            hints=hints,
            dependencies=dependencies,
        )
        if candidate is None:
            continue

        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
            raise DependencyResolutionError(
                f"Pozisyonel-only parametre inject edilemez: {name}."
            )

        if name in dependencies:
            used_dependency_names.add(name)
        candidates.append(candidate)

    unused = set(dependencies) - used_dependency_names
    if unused:
        missing = ", ".join(sorted(unused))
        raise DependencyResolutionError(
            f"Dependency override parametresi callable imzasında yok: {missing}."
        )

    return _InjectionPlan(signature=signature, candidates=tuple(candidates))


def make_inject_candidate(
    *,
    name: str,
    parameter: inspect.Parameter,
    hints: Mapping[str, Any],
    dependencies: Mapping[str, Token],
) -> _InjectCandidate | None:
    if name in dependencies:
        return _InjectCandidate(name=name, token=dependencies[name], explicit=True)

    annotation = hints.get(name, parameter.annotation)
    marker = marker_from_annotation(annotation)
    if isinstance(parameter.default, Inject):
        marker = parameter.default

    annotation_token = token_from_annotation(annotation)

    if marker is not None:
        token = marker.token
        optional = marker.optional

        if token is MISSING and annotation_token is not None:
            token = annotation_token.token
            optional = optional or annotation_token.optional

        if token is MISSING or token is inspect.Parameter.empty:
            raise DependencyResolutionError(
                f"{name} parametresi Inject(token) veya typehint gerektiriyor."
            )

        ensure_hashable_token(token)
        return _InjectCandidate(
            name=name,
            token=token,
            explicit=True,
            optional=optional,
        )

    if annotation_token is None:
        return None

    if parameter.default is not inspect.Parameter.empty:
        return None

    return _InjectCandidate(
        name=name,
        token=annotation_token.token,
        explicit=False,
        optional=annotation_token.optional,
    )


def choose_token(*, token: Token | None, abstract: Token | None) -> Token | None:
    if token is not None and abstract is not None:
        raise DependencyResolutionError("token ve abstract aynı anda verilmemeli.")
    selected = abstract if abstract is not None else token
    if selected is not None:
        ensure_hashable_token(selected)
    return selected


def choose_factory(
    *,
    provider: Any,
    concrete: Any,
    factory: Any,
    instance: Any,
) -> Any:
    supplied = [
        value is not MISSING
        for value in (provider, concrete, factory, instance)
    ].count(True)

    if supplied > 1:
        raise DependencyResolutionError(
            "provider, concrete, factory ve instance seçeneklerinden yalnızca biri verilmeli."
        )

    if instance is not MISSING:
        return instance
    if factory is not MISSING:
        return factory
    if concrete is not MISSING:
        return concrete
    if provider is not MISSING:
        return provider
    return MISSING


def infer_token_from_factory(factory: Any) -> Token | None:
    if inspect.isclass(factory):
        ensure_hashable_token(factory)
        return factory
    if is_direct_instance(factory):
        token = type(factory)
        ensure_hashable_token(token)
        return token
    if callable(factory):
        hints = safe_type_hints(factory)
        return_annotation = hints.get("return", inspect.Signature.empty)
        annotation_token = token_from_annotation(return_annotation)
        if annotation_token is not None:
            ensure_hashable_token(annotation_token.token)
            return annotation_token.token
    return None


def is_direct_instance(value: Any) -> bool:
    return not inspect.isclass(value) and not callable(value)


def normalize_dependencies(dependencies: DependencyMap | None) -> dict[str, Token]:
    if dependencies is None:
        return {}
    normalized: dict[str, Token] = {}
    for name, token in dependencies.items():
        if not isinstance(name, str) or not name:
            raise DependencyResolutionError(
                "dependencies anahtarları boş olmayan string olmalı."
            )
        ensure_hashable_token(token)
        normalized[name] = token
    return normalized


def merge_dependencies(
    dependencies: DependencyMap | None,
    named_dependencies: Mapping[str, Token],
) -> dict[str, Token]:
    merged = normalize_dependencies(dependencies)
    for name, token in named_dependencies.items():
        if name in merged and merged[name] != token:
            raise DependencyResolutionError(
                f"{name} dependency override değeri iki kez farklı verildi."
            )
        ensure_hashable_token(token)
        merged[name] = token
    return merged


def dependency_items(dependencies: Mapping[str, Token]) -> tuple[tuple[str, Token], ...]:
    return tuple(sorted(dependencies.items(), key=lambda item: item[0]))


def normalize_priority(priority: int) -> int:
    if not isinstance(priority, int):
        raise DependencyResolutionError("priority int olmalı.")
    return priority


def ensure_hashable_token(token: Any) -> None:
    try:
        hash(token)
    except TypeError as exc:
        raise DependencyResolutionError(
            f"Dependency token hashable olmalı: {token!r}."
        ) from exc


def marker_from_annotation(annotation: Any) -> Inject | None:
    if get_origin(annotation) is Annotated:
        for metadata in get_args(annotation)[1:]:
            if isinstance(metadata, Inject):
                return metadata
    return None


def token_from_annotation(annotation: Any) -> _AnnotationToken | None:
    if annotation is inspect.Parameter.empty:
        return None

    if get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]

    annotation, optional = unwrap_optional_annotation(annotation)

    if annotation is inspect.Parameter.empty:
        return None

    ensure_hashable_token(annotation)
    return _AnnotationToken(token=annotation, optional=optional)


def unwrap_optional_annotation(annotation: Any) -> tuple[Any, bool]:
    origin = get_origin(annotation)
    if origin not in (Union, UnionType):
        return annotation, False

    args = get_args(annotation)
    non_none = tuple(item for item in args if item is not NONE_TYPE)

    if len(non_none) == 1 and len(non_none) != len(args):
        return non_none[0], True

    return annotation, False


@functools.lru_cache(maxsize=4096)
def safe_type_hints(func: Callable[..., Any]) -> Mapping[str, Any]:
    target = func
    if inspect.isclass(func):
        target = func.__init__
    elif not inspect.isfunction(func) and not inspect.ismethod(func):
        call = getattr(func, "__call__", None)
        if call is not None:
            target = call

    try:
        return get_type_hints(target, include_extras=True)
    except Exception:
        return {}


def can_autowire(token: Any) -> bool:
    return (
        inspect.isclass(token)
        and token.__module__ != "builtins"
        and not inspect.isabstract(token)
    )


def cached_instances(provider: _Provider) -> tuple[Any, ...]:
    instances: list[Any] = []
    if provider.instance is not MISSING:
        instances.append(provider.instance)
    if provider.singleton is not MISSING:
        instances.append(provider.singleton)
    return tuple(instances)


def enter_resolution(token: Token) -> contextvars.Token[tuple[Token, ...]]:
    stack = _resolution_stack.get()
    if token in stack:
        cycle = " -> ".join(format_token(item) for item in (*stack, token))
        raise CircularDependencyError(f"Döngüsel dependency tespit edildi: {cycle}")
    return _resolution_stack.set((*stack, token))


def exit_resolution(stack_token: contextvars.Token[tuple[Token, ...]]) -> None:
    _resolution_stack.reset(stack_token)


async def maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def close_awaitable(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        close()


def format_token(token: Any) -> str:
    if inspect.isclass(token):
        module = getattr(token, "__module__", "")
        name = getattr(token, "__qualname__", token.__name__)
        if module and module != "builtins":
            return f"{module}.{name}"
        return name

    name = getattr(token, "__qualname__", None) or getattr(token, "__name__", None)
    if name:
        return str(name)
    return repr(token)


__all__ = [
    "AsyncDependencyError",
    "BaseDependencyContainer",
    "CircularDependencyError",
    "DEFAULT_PRIORITY",
    "DependencyContainer",
    "DependencyError",
    "DependencyMap",
    "DependencyNotFoundError",
    "DependencyResolutionError",
    "Inject",
    "Lifetime",
    "Token",
    "get_current_container",
    "get_default_container",
    "injection",
    "set_default_container",
]
