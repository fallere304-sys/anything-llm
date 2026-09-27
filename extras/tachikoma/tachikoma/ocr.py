"""目 (OCR)。行単位の画像 → 文字列。Vision Encoder-Decoder (manga-ocr 系) を transformers で動かす。

行の切り出し (検出) は学習では不要: 文字情報付き PDF から、行ごとの位置と正解の文字列が取れるため。
"""


class VisionOCR:
    def __init__(self, cfg, model=None):
        self.cfg = cfg
        self.load(model or cfg["ocr_model"])

    def load(self, model):
        import torch
        from transformers import AutoImageProcessor, AutoTokenizer, VisionEncoderDecoderModel
        self.torch = torch
        self.device = "cuda" if self.cfg["ocr_device"] == "cuda" and torch.cuda.is_available() else "cpu"
        if self.device == "cpu":
            torch.set_num_threads(self.cfg["ocr_cpu_threads"])
        self.processor = AutoImageProcessor.from_pretrained(model)
        self.tokenizer = AutoTokenizer.from_pretrained(model)
        self.model = VisionEncoderDecoderModel.from_pretrained(model).to(self.device).eval()
        gc = self.model.generation_config
        if gc.decoder_start_token_id is None:   # 古い形式で保存されたモデルの補完
            gc.decoder_start_token_id = (self.model.config.decoder_start_token_id
                                         or self.tokenizer.cls_token_id or self.tokenizer.bos_token_id)
        if gc.pad_token_id is None:
            gc.pad_token_id = self.tokenizer.pad_token_id
        if gc.eos_token_id is None:
            gc.eos_token_id = self.tokenizer.sep_token_id or self.tokenizer.eos_token_id
        self.name = model

    def recognize(self, image_path):
        from PIL import Image
        img = Image.open(image_path).convert("L").convert("RGB")
        pixels = self.processor(img, return_tensors="pt").pixel_values.to(self.device)
        with self.torch.no_grad():
            out = self.model.generate(pixels, max_length=self.cfg["ocr_max_len"], num_beams=1)
        text = self.tokenizer.decode(out[0], skip_special_tokens=True)
        return "".join(text.split())     # 文字単位トークナイザが入れる空白を除く
