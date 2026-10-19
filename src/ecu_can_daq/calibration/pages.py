"""Calibration page switching scaffold."""
from __future__ import annotations

from enum import Enum


class CalPage(Enum):
    REFERENCE = 0
    WORKING = 1


class PageManager:
    """Scaffold for GET_PAG/SET_CAL_PAGE etc."""

    def __init__(self, transport):
        self._transport = transport
        self._current_page = CalPage.WORKING

    @property
    def current_page(self) -> CalPage:
        return self._current_page

    def set_page(self, page: CalPage) -> None:
        # TODO: implement XCP SET_CAL_PAGE
        self._current_page = page

    def copy_page(self, src: CalPage, dst: CalPage) -> None:
        # TODO: implement XCP COPY_CAL_PAGE
        pass
