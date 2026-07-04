# plugin-voice — Voice Dojo Report

Matrix: 10 ElevenLabs premade voices, each enrolled as owner in turn.
Trials: 30 genuine / 270 impostor.

- genuine scores:  mean **2.655**, min -0.198
- impostor scores: mean **-0.235**, max 2.549
- **EER ≈ 6.7%** at threshold 1.210
- operating point (FAR ≤ 5%): threshold **1.353** → FAR 4.8%, FRR 6.7%

Voices: Roger - Laid-Back, Casual, Resonant, Sarah - Mature, Reassuring, Confident, Laura - Enthusiast, Quirky Attitude, Charlie - Deep, Confident, Energetic, George - Warm, Captivating Storyteller, Callum - Husky Trickster, River - Relaxed, Neutral, Informative, Harry - Fierce Warrior, Liam - Energetic, Social Media Creator, Alice - Clear, Engaging Educator

Recognizer: 24 log-mel bands, voiced-frame stats (shape+std), cosine vs
enrollment-mean profile (`plugin_voice/dsp.py`). Clean-TTS numbers — real
microphones will be noisier; the verdict stays advisory by design.
