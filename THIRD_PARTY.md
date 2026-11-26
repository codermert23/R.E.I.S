# Third-party materials

## yazgı-tts-1 (Turkish TTS model, optional runtime download)

- Model and code: `erkamk/yazgi-tts-1` on Hugging Face (https://huggingface.co/erkamk/yazgi-tts-1), pinned to
  revision `87c89ad84556fb3f1c171f7f88a37ddc0f806b04`. Copyright 2026 Erkam Kavak.
- License: Apache License 2.0 (code and weights). Not bundled with Reis: the user installs it on request
  ("Ses motorunu kur") into `%LOCALAPPDATA%\Reis\runtime\yazgi` (PyTorch CPU, normalizer-tr 0.7.0, numpy,
  soundfile) and `%LOCALAPPDATA%\Reis\models\yazgi`. Reis only runs it as a separate subprocess.
- Notice text from the model's NOTICE file: the model was trained with the following data and tools, credited
  under their licenses:
  - Alania Synthetic Speech TR (cloud0day3/alania-synthetic-speech-tr), CC BY 4.0 (speech generated with
    VoxCPM2; part of its text from FineWeb-2, ODC-By 1.0)
  - YODAS2 (espnet/yodas2), subset tr000, CC BY 3.0; each video by its uploader, listed in the model's
    ATTRIBUTION.md (https://huggingface.co/erkamk/yazgi-tts-1/blob/main/ATTRIBUTION.md); Li et al., "YODAS:
    Youtube-Oriented Dataset for Audio and Speech", 2023
  - Common Voice 21.0 (Mozilla Foundation; Ardila et al., 2020), CC0 1.0
  - MediaSpeech (OpenSLR 108, Kolobov et al., 2021), CC BY 4.0
  - FLEURS (Google; Conneau et al., 2022), CC BY 4.0
  - normalizer-tr, Apache-2.0 (text front end)
  - SpeechBrain spkrec-resnet-voxceleb, Apache-2.0 (speaker embeddings)
- PyTorch (BSD-3-Clause) is installed from download.pytorch.org into the separate runtime environment only.
