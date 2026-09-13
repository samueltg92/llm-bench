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
    # Extensions are installed Python packages, never code loaded from a prompt/export.
    from importlib.metadata import entry_points

    matches = list(entry_points(group="llm_bench.providers", name=model.provider))
    if len(matches) != 1:
        raise ValueError("Unknown or ambiguous provider adapter: " + model.provider)
    return matches[0].load()(model, timeouts)
