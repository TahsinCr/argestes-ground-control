# Target Lock modeli

Bu klasördeki `yolo26s.pt`, Ultralytics'in resmi YOLO26s COCO ağırlığıdır.
YKİ, COCO `airplane` sınıfını (`class_id=4`) hedef adayı olarak kullanır.

- Kaynak: https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26s.pt
- PT SHA-256: `646f8bc3fe0a656803d95c294f7852321748cb29d13466a1af8862e2db384a1b`
- Model metadata lisansı: Ultralytics AGPL-3.0; dağıtım/deployment
  kararında projenin lisans uyumu ayrıca kontrol edilmelidir.
- `yolo26s.onnx`, bu PT dosyasından Ultralytics `8.4.103` ile 640x640,
  opset 20 ve simplify kapalı olacak şekilde export edilmiştir.
- ONNX SHA-256: `2591c826cdb7b51b7f27d90dd312456794fd744d2e37ea1587a0b1c0c03a665e`

ONNX dosyası masaüstü YKİ'daki ONNX Runtime backend'i içindir. Jetson
Orin tarafında aynı PT ağırlığı TensorRT engine'e dönüştürülebilir. Bu genel
amaçlı model bir başlangıç noktasıdır; yarışma uçuş profiline ait SİHA
görüntüleriyle doğrulanmadan veya fine-tune edilmeden yarışma başarımı garanti
edilmez.
