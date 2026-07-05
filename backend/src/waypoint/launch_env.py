from typing import Annotated, Any

from pydantic import BeforeValidator


def validate_launch_env(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError("launch env must be a mapping of variable names to values")
    env: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or not raw_key:
            raise ValueError("launch env variable names must be non-empty strings")
        if "=" in raw_key or "\x00" in raw_key:
            raise ValueError(f"invalid launch env variable name: {raw_key!r}")
        if raw_value is None:
            raise ValueError(f"launch env variable {raw_key!r} cannot be null")
        coerced = str(raw_value)
        if "\x00" in coerced:
            raise ValueError(f"launch env variable {raw_key!r} cannot contain NUL")
        env[raw_key] = coerced
    return env


LaunchEnv = Annotated[dict[str, str], BeforeValidator(validate_launch_env)]
