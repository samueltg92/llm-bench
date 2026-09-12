def create(model, timeouts=None):
    if model.provider == "openai_compat":
        from .openai_compat import OpenAICompat

        return OpenAICompat(model, timeouts)
    if model.provider == "google_genai":
        from .google_genai import GoogleGenAI

        return GoogleGenAI(model, timeouts)
    if model.provider == "fake":
        from .fake import FakeProvider

        return FakeProvider()
    raise ValueError("Unknown provider")
