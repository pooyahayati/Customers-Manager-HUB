# pyright: reportPrivateUsage=false
from customers_manager_hub.platform_ai import _parse_gemini_models, _parse_openai_models


def test_openai_model_discovery_classifies_supported_tasks() -> None:
    models = _parse_openai_models(
        {
            "data": [
                {"id": "gpt-text-model"},
                {"id": "gpt-transcribe"},
                {"id": "text-embedding-model"},
                {"id": "image-model"},
            ]
        }
    )

    capabilities = {model.id: model.capabilities for model in models}
    assert capabilities == {
        "gpt-text-model": ["generation"],
        "gpt-transcribe": ["transcription"],
        "image-model": [],
        "text-embedding-model": ["embedding"],
    }


def test_gemini_generate_content_models_are_available_for_audio_transcription() -> None:
    models = _parse_gemini_models(
        {
            "models": [
                {
                    "name": "models/gemini-multimodal",
                    "displayName": "Gemini Multimodal",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {
                    "name": "models/gemini-embedding",
                    "supportedGenerationMethods": ["embedContent"],
                },
            ]
        }
    )

    capabilities = {model.id: model.capabilities for model in models}
    assert capabilities == {
        "gemini-embedding": ["embedding"],
        "gemini-multimodal": ["generation", "transcription"],
    }
