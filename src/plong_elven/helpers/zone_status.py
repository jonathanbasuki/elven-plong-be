def classify(count: int, warning_threshold: int, crowded_threshold: int) -> str:
    if count < warning_threshold:
        return "safe"
    if count < crowded_threshold:
        return "warning"
    return "crowded"
