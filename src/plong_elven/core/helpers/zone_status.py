def classify(count: int, threshold: int) -> str:
    """'safe' below the zone's threshold, 'crowded' at or above it."""
    return "safe" if count < threshold else "crowded"
