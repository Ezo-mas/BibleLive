"""Scripture search routes: /api/books and /api/lookup."""

from fastapi import APIRouter, Depends, HTTPException, Query

from ...book_names import lithuanian_search_variants
from ...books import CANONICAL_BOOKS
from ...config import config
from ...passage import PassageError, resolve_passage
from ..auth import verify_operator_token
from ..models import BookInfo, BooksResponse, ErrorResponse, PassageResponse, VerseText
from ..state import AppState


def build_router(app_state: AppState) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/books",
        response_model=BooksResponse,
        responses={
            200: {"description": "The canonical 66-book list"},
            401: {"model": ErrorResponse, "description": "Unauthorized"},
            403: {"model": ErrorResponse, "description": "Forbidden"},
        },
        dependencies=[Depends(verify_operator_token)],
    )
    async def list_books() -> BooksResponse:
        """List the canonical books for the operator's passage search.

        Served from the same canon the parser, importer, and database use, so the
        front end never carries a copy that could drift — including each book's
        abbreviation and the name variants the parser accepts, which is what lets
        the search box match ``ge``, ``ps``, or ``canticles`` without a second
        name table on the client. Requires authentication if
        BIBLELIVE_REQUIRE_AUTH is enabled.
        """
        # The names the loaded translation gives its books (none for KJV). The
        # Lithuanian parser's spellings are added only when that translation is the
        # Lithuanian one, so they can never match books named in another language.
        local_names = app_state.book_names.for_database(app_state.bible_lookup.db_path)
        lithuanian = config.LANGUAGE_BIBLE_VERSIONS.get("lt")
        local_variants = (
            lithuanian_search_variants()
            if local_names and lithuanian and app_state.active_bible_version() == lithuanian
            else {}
        )
        return BooksResponse(
            books=[
                BookInfo(
                    name=book.name,
                    chapters=book.chapters,
                    order=book.order,
                    abbrev=book.abbrev,
                    variants=list(book.variants),
                    local_name=local_names.get(book.name),
                    local_variants=local_variants.get(book.name, []),
                )
                for book in CANONICAL_BOOKS
            ]
        )

    @router.get(
        "/api/lookup",
        response_model=PassageResponse,
        responses={
            200: {"description": "Passage resolved to verse text"},
            401: {"model": ErrorResponse, "description": "Unauthorized"},
            403: {"model": ErrorResponse, "description": "Forbidden"},
            404: {"model": ErrorResponse, "description": "No such book, chapter, or verse"},
            422: {"model": ErrorResponse, "description": "Invalid passage parameters"},
        },
        dependencies=[Depends(verify_operator_token)],
    )
    async def lookup_passage(
        book: str = Query(..., min_length=1, max_length=100, description="Canonical book name"),
        chapter: int = Query(..., gt=0, le=200, description="Chapter number"),
        verse_start: int | None = Query(None, gt=0, description="First verse"),
        verse_end: int | None = Query(None, gt=0, description="Last verse (inclusive)"),
    ) -> PassageResponse:
        """Resolve a book/chapter/verse passage to display-ready text.

        Read-only: it consults the canonical canon and the server's Bible
        database and returns the verses, so the console can show the operator what
        would be displayed before they stage it. The same resolver backs the
        ``display_reference`` WebSocket action, so a rejected request reads the
        same either way. Requires authentication if BIBLELIVE_REQUIRE_AUTH is
        enabled.
        """
        try:
            passage = resolve_passage(
                app_state.bible_lookup,
                book=book,
                chapter=chapter,
                verse_start=verse_start,
                verse_end=verse_end,
            )
        except PassageError as e:
            raise HTTPException(status_code=e.status_code, detail=e.message)

        return PassageResponse(
            book=passage.book,
            chapter=passage.chapter,
            verse_start=passage.verse_start,
            verse_end=passage.verse_end,
            verses=[VerseText(verse=verse, text=text) for verse, text in passage.verses],
            text=passage.text,
        )

    return router
