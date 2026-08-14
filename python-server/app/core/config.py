"""Central configuration.

The one module in python-server/ that reads the environment. Everything else
imports the `settings` instance from here.
"""
from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings

PLAIN_SCHEME = 'postgresql://'
ASYNC_SCHEME = 'postgresql+psycopg://'


class Settings(BaseSettings):
    """Values read from the process environment.

    No defaults on purpose: a missing variable has to surface as a startup
    failure, not be papered over with a localhost guess that silently points the
    server at the wrong database.
    """

    database_url: str = Field(min_length=1)

    @field_validator('database_url')
    @classmethod
    def use_async_dialect(cls, value: str) -> str:
        """Rewrite the plain scheme to the async driver.

        Lets docker-compose.yml keep a stock `postgresql://` DATABASE_URL while
        nothing outside this module has to know about dialect strings. A URL that
        already names a driver is left alone.
        """
        if value.startswith(PLAIN_SCHEME):
            return value.replace(PLAIN_SCHEME, ASYNC_SCHEME, 1)
        return value


def load_settings() -> Settings:
    """Build the settings, translating pydantic's field errors into a message
    that names the environment variables the operator actually has to set."""
    try:
        return Settings()
    except ValidationError as error:
        problems = '; '.join(
            f"{'.'.join(str(part) for part in problem['loc']).upper()}: {problem['msg']}"
            for problem in error.errors()
        )
        raise RuntimeError(f'Invalid environment configuration -- {problems}') from error


settings = load_settings()
