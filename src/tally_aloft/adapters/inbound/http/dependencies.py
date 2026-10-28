from typing import Annotated

from fastapi import Depends, Request

from tally_aloft.application.registry import UseCases


def get_use_cases(request: Request) -> UseCases:
    """Return the `UseCases` the lifespan put on the app."""
    use_cases: object = getattr(request.app.state, "use_cases", None)
    if not isinstance(use_cases, UseCases):
        msg = "app.state.use_cases isn't set; the app's lifespan hasn't run"
        raise RuntimeError(msg)  # noqa: TRY004  # A wiring fault, not a bad argument.
    return use_cases


# How every router reaches the use cases.
UseCasesDep = Annotated[UseCases, Depends(get_use_cases)]
