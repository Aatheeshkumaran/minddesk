# MindDesk — Pixel Robot Cat Edition

Local MindDesk app with FastAPI + Ollama, guest mode, account sign-in, dark/light themes, and a custom Three.js robot-cat companion inspired by the supplied character sheet.

Pixel's design uses a white robot shell, dark screen face, neon-green pixel eyes and smile, navy suit, green tie/indicators, cat ears, rounded paws and feet. The 3D model is generated from geometry in the browser; no external character image is used.

## Run on Windows
1. Extract this folder.
2. Keep Ollama running and verify models with `ollama list`.
3. Run `run.bat`.
4. Open http://127.0.0.1:8000.

The Three.js module is loaded from jsDelivr, so the browser needs internet access to load the 3D engine. Ollama model inference remains local.
