from __future__ import annotations

import inspect
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeVar, get_type_hints


T = TypeVar("T")
Token = object


class DependencyError(RuntimeError):
    pass


class DependencyNotFoundError(DependencyError):
    pass


class CircularDependencyError(DependencyError):
    pass


class Lifetime(StrEnum):
    TRANSIENT = "transient"
    SINGLETON = "singleton"
    SCOPED = "scoped"


_MISSING = object()


@dataclass(slots=True)
class _Provider:
    factory: Callable[..., Any]
    lifetime: Lifetime
    dependencies: dict[str, Token] = field(default_factory=dict)
    instance: Any = _MISSING
    lock: threading.RLock = field(default_factory=threading.RLock)


class DependencyContainer:
    """Constructor injection ve yaşam döngüsü destekleyen küçük DI container."""

    def __init__(self, *, parent: "DependencyContainer | None" = None) -> None:
        self._parent = parent
        self._providers: dict[Token, _Provider] = {}
        self._scope: dict[Token, Any] = {}
        self._resolving = threading.local()

    def register(
        self,
        token: Token,
        provider: Callable[..., Any] | None = None,
        *,
        lifetime: Lifetime = Lifetime.TRANSIENT,
        dependencies: dict[str, Token] | None = None,
    ) -> "DependencyContainer":
        factory = provider or token
        if not callable(factory):
            raise TypeError(f"Provider callable olmalı: {factory!r}")
        self._providers[token] = _Provider(
            factory=factory,
            lifetime=lifetime,
            dependencies=dict(dependencies or {}),
        )
        return self

    def singleton(
        self,
        token: Token,
        provider: Callable[..., Any] | None = None,
        *,
        dependencies: dict[str, Token] | None = None,
    ) -> "DependencyContainer":
        return self.register(
            token,
            provider,
            lifetime=Lifetime.SINGLETON,
            dependencies=dependencies,
        )

    def transient(
        self,
        token: Token,
        provider: Callable[..., Any] | None = None,
        *,
        dependencies: dict[str, Token] | None = None,
    ) -> "DependencyContainer":
        return self.register(token, provider, dependencies=dependencies)

    def scoped(
        self,
        token: Token,
        provider: Callable[..., Any] | None = None,
        *,
        dependencies: dict[str, Token] | None = None,
    ) -> "DependencyContainer":
        return self.register(
            token,
            provider,
            lifetime=Lifetime.SCOPED,
            dependencies=dependencies,
        )

    def instance(self, token: Token, value: Any) -> "DependencyContainer":
        self._providers[token] = _Provider(
            factory=lambda: value,
            lifetime=Lifetime.SINGLETON,
            instance=value,
        )
        return self

    def create_scope(self) -> "DependencyContainer":
        return DependencyContainer(parent=self)

    def resolve(self, token: type[T]) -> T:
        owner, provider = self._find(token)
        if provider is None:
            if inspect.isclass(token) and not inspect.isabstract(token):
                provider = _Provider(token, Lifetime.TRANSIENT)
                owner = self
            else:
                raise DependencyNotFoundError(f"Dependency bulunamadı: {token!r}")

        stack: list[Token] = getattr(self._resolving, "stack", [])
        if token in stack:
            chain = " -> ".join(getattr(item, "__name__", repr(item)) for item in (*stack, token))
            raise CircularDependencyError(chain)
        self._resolving.stack = [*stack, token]
        try:
            if provider.instance is not _MISSING:
                return provider.instance
            if provider.lifetime == Lifetime.SCOPED and token in self._scope:
                return self._scope[token]
            if provider.lifetime == Lifetime.SINGLETON:
                with provider.lock:
                    if provider.instance is _MISSING:
                        provider.instance = self._build(provider)
                    return provider.instance
            value = self._build(provider)
            if provider.lifetime == Lifetime.SCOPED:
                self._scope[token] = value
            return value
        finally:
            self._resolving.stack = stack

    def _find(self, token: Token) -> tuple["DependencyContainer", _Provider | None]:
        provider = self._providers.get(token)
        if provider is not None:
            return self, provider
        if self._parent is not None:
            return self._parent._find(token)
        return self, None

    def _build(self, provider: _Provider) -> Any:
        target = provider.factory
        signature = inspect.signature(target)
        try:
            hints = get_type_hints(target.__init__ if inspect.isclass(target) else target)
        except (NameError, TypeError):
            hints = {}
        kwargs: dict[str, Any] = {}
        for name, parameter in signature.parameters.items():
            if name in {"self", "cls"} or parameter.kind in {
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            }:
                continue
            dependency = provider.dependencies.get(name, hints.get(name))
            if dependency is None:
                if parameter.default is inspect.Parameter.empty:
                    raise DependencyNotFoundError(
                        f"{target!r}.{name} için type hint veya açık dependency gerekli"
                    )
                continue
            kwargs[name] = self.resolve(dependency)
        return target(**kwargs)

    def shutdown(self) -> None:
        instances = [*self._scope.values()]
        self._scope.clear()
        if self._parent is None:
            instances.extend(
                provider.instance
                for provider in self._providers.values()
                if provider.instance is not _MISSING
            )
        seen: set[int] = set()
        for instance in reversed(instances):
            if id(instance) in seen:
                continue
            seen.add(id(instance))
            close = getattr(instance, "close", None)
            stop = getattr(instance, "stop", None)
            if callable(close):
                close()
            elif callable(stop):
                stop()
