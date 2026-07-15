from cowork.harnesses.cli_agents.antigravity import friendly_error

def test_friendly_error_quota_matched():
    stderr = "Error: Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 25h40m39s."
    assert friendly_error(stderr) == "Antigravity quota exhausted — resets in 25h40m39s."

def test_friendly_error_unrelated():
    assert friendly_error("Error: command not found") is None

def test_friendly_error_empty():
    assert friendly_error("") is None
