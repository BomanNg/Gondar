"""Util that calls PubMed Entrez."""
import logging
from copy import deepcopy
from typing import Callable, Dict, Generator, Iterator, List

from bs4 import BeautifulSoup
from pydantic import BaseModel, ValidationError, model_validator

from gondar import Gconfig
from gondar.utils.types import POS_INT, STR, VALID

logger = logging.getLogger(__name__)

filter_meta: Callable[[BeautifulSoup | None, str], str] = (
    lambda content, linker: linker.join(content.stripped_strings)
    if content is not None
    else ""
)


def get_Meta(article: BeautifulSoup) -> Dict[str, str]:
    """Get metadata from soup"""

    return {
        "article": filter_meta(article.find("article-title"), " "),
        "journal": filter_meta(article.find("journal-title"), " "),
        "doi": filter_meta(
            article.find("article-id", attrs={"pub-id-type": "doi"}), ""
        ),
        "pubdate": filter_meta(
            article.find("pub-date", attrs={"pub-type": "epub"}), "/"
        ),
    }


def get_Body(article: BeautifulSoup) -> Dict[str, List]:
    """Get body text from soup"""

    article_cp = deepcopy(article)

    sections: List[BeautifulSoup] = list(
        section for section in article_cp.find_all("sec")
    )

    for sec in sections:
        for table_wrap in sec.find_all("table-wrap"):
            table_wrap.decompose()

    section_contents: Generator[str] = (
        " ".join(section.stripped_strings) if section is not None else ""
        for section in sections
    )

    return {
        "body": list(section_contents),
    }


def removeAllAttrs(soup: BeautifulSoup):
    """
    Recursively remove all attributes.
    This step is crucial for saving tokens usage (and your dollars).
    """
    if hasattr(soup, "attrs"):
        soup.attrs = {}
    if hasattr(soup, "contents"):
        for child in soup.contents:
            removeAllAttrs(child)


def get_Tables(article: BeautifulSoup) -> Dict[str, List]:
    """Get tables from soup"""

    tables: Generator[BeautifulSoup] = (
        table for table in article.find_all("table-wrap")
    )

    unwraped_tables: List[str] = []
    for t in tables:
        removeAllAttrs(t)
        unwraped_tables.append(str(t))

    return {
        "tables": unwraped_tables,
    }


class EntrezAPIWrapper(BaseModel):
    """Wrapper around Entrez API."""

    esearch: Callable | None  #: :meta private:
    efetch: Callable | None  #: :meta private:
    parse_methods: List[Callable] = [get_Meta, get_Body, get_Tables]

    restart: POS_INT = 0
    retmax: POS_INT = 3
    retmode: STR = "xml"
    rettype: STR = "uilist"
    sort: STR = "relevance"
    datetype: STR = "pdat"
    reldate: STR = None
    mindate: STR = None
    maxdate: STR = None

    db: STR = "pmc"
    Id_tag: STR = "Id"
    article_tag: STR = "article"

    @model_validator(mode="before")
    @classmethod
    def validate_env_before(cls, values: Dict) -> Dict:
        """Validate that the Biopython is exists in environment."""
        try:
            from Bio import Entrez

            values["esearch"] = Entrez.esearch
            values["efetch"] = Entrez.efetch

        except ImportError:
            raise ImportError(
                """
                Could not import Entrez from Biopython package. 
                """
            )

        try:
            Entrez.email = Gconfig.EMAIL

        except ValidationError:
            raise ValidationError(
                """
                Could not provide email for Entrez.
                Please add your email in Gconfig with 'EMAIL=your@email.com'.
                """
            )

        return values

    @model_validator(mode="after")
    def validate_env_after(self) -> VALID:
        """Validate that the lxml is exists in environment if mode is xml"""
        if self.retmode == "xml":
            try:
                import lxml

            except ImportError:
                raise ImportError(
                    """
                    Could not import lxml. 
                    """
                )

    def _search_ID(self, query: str) -> List[str | None]:
        try:
            esearch_params = {
                "restart": self.restart,
                "retmax": self.retmax,
                "retmode": self.retmode,
                "rettype": self.rettype,
                "sort": self.sort,
                "datetype": self.datetype,
                "reldate": self.reldate,
                "mindate": self.mindate,
                "maxdate": self.maxdate,
            }
            with self.esearch(db=self.db, term=query, **esearch_params) as handle:
                searchResults = BeautifulSoup(handle.read(), self.retmode)

                return [id_tag.text for id_tag in searchResults.find_all(self.Id_tag)]

        except Exception as e:
            logger.error(f"Entrez exception: \n{e} \neSearch failed and return []. ")
            return []

    def _fetch_content(self, ids: List[str]) -> BeautifulSoup | None:
        try:
            with self.efetch(db=self.db, id=ids) as handle:
                return BeautifulSoup(handle.read(), self.retmode)

        except Exception as e:
            logger.error(f"Entrez exception: \n{e}\neFetch failed and return None")
            return None

    def _parse_article(self, article: BeautifulSoup) -> Dict:
        result = {}
        for method in self.parse_methods:
            result.update(method(article))

        return result

    def run(self, query: str) -> str:
        exception_msg = "No useable article was found. "

        ids: List[str | None] = self._search_ID(query=query)
        if ids == []:
            return exception_msg

        content: BeautifulSoup | None = self._fetch_content(ids=ids)
        if content is None:
            return exception_msg

        parsed_articles: Generator[Dict] = (
            self._parse_article(article=article)
            for article in content.find_all(self.article_tag)
        )

        _merge_result: Callable[[Dict], str] = lambda res: "\n".join(
            ("\n".join(res[key]) for key in res.keys())
        )

        return "\n\n".join([_merge_result(article) for article in parsed_articles])

    def load(self, query: str) -> List[Dict]:
        ids: List[str | None] = self._search_ID(query=query)
        if ids is []:
            return Exception("Found no any article id of query. ")

        content: BeautifulSoup | None = self._fetch_content(ids=ids)
        if content is None:
            return Exception("Found no any article content of query. ")

        parsed_articles: Generator[Dict] = (
            self._parse_article(article=article)
            for article in content.find_all(self.article_tag)
        )

        return list(parsed_articles)
