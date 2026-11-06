from dataclasses import dataclass
import os

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    tavily_api_key: str
    openweather_api_key: str
    openrouter_api_key: str
    openrouter_model: str

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        required = {
            "TAVILY_API_KEY": os.getenv("TAVILY_API_KEY"),
            "OPENWEATHER_API_KEY": os.getenv("OPENWEATHER_API_KEY"),
            "OPENROUTER_API_KEY": os.getenv("OPENROUTER_API_KEY"),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            names = ", ".join(missing)
            raise ValueError(f"Missing required environment variables: {names}")

        return cls(
            tavily_api_key=required["TAVILY_API_KEY"] or "",
            openweather_api_key=required["OPENWEATHER_API_KEY"] or "",
            openrouter_api_key=required["OPENROUTER_API_KEY"] or "",
            openrouter_model=os.getenv(
                "OPENROUTER_MODEL", "nvidia/nemotron-3.5-lightning:free"
            ),
        )
