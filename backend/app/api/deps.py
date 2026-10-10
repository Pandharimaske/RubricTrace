"""FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from backend.app.container import Container
from fastapi import Depends, Request


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]
