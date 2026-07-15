"""Entry point: ``python -m cowork`` boots the server."""
import sys

def _format_corruption_message(module_name: str) -> str:
    dist = module_name.replace("_", "-")
    return f"venv corrupted ({module_name}): run  uv sync --reinstall-package {dist}"

try:
    from cowork.cli import main
except ModuleNotFoundError as e:
    print(_format_corruption_message(e.name))
    sys.exit(2)

if __name__ == "__main__":
    main()
