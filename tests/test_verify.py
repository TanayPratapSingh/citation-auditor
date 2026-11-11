"""verify.check against a fake Lookup: no network. Each case is a bug seen on real data."""

from pathlib import Path

from auditor.parse import Reference
from auditor.verify import Registry, check

NO_REGISTRY = Registry(Path("/nonexistent.parquet"))
WAKEFIELD = "Ileal-lymphoid-nodular hyperplasia, non-specific colitis, and pervasive developmental disorder in children"


def item(doi, title, year, family="Wakefield", type_="journal-article", **kw):
    return {"DOI": doi, "title": [title], "issued": {"date-parts": [[year]]}, "type": type_,
            "author": [{"family": family}], **kw}


class FakeLookup:
    calls = 0

    def __init__(self, works=None, search=None, registered=()):
        self.works, self.search, self.registered = works or {}, search or [], set(registered)

    def crossref_work(self, doi):
        return self.works.get(doi)

    def crossref_search(self, text, rows=5):
        return self.search

    def openalex_work(self, doi):
        return None

    def openalex_search(self, text, rows=5):
        return []

    def doi_registered(self, doi):
        return doi in self.registered

    def pubmed_abstract(self, pmid):
        return None


def ref(text, doi=None, year=1998, surname="wakefield"):
    return Reference(1, text, doi, year, surname)


RETRACTION = {"DOI": "10.1016/notice", "type": "retraction", "label": "Retraction", "source": "retraction-watch",
              "updated": {"date-time": "2010-02-06T00:00:00Z"}}


def test_unflagged_duplicate_doi_inherits_the_retraction_of_its_twin():
    dup = item("10.1016/dup", WAKEFIELD, 1998)
    original = item("10.1016/orig", "RETRACTED: " + WAKEFIELD, 1998, **{"updated-by": [RETRACTION]})
    lk = FakeLookup(works={"10.1016/dup": dup}, search=[original, dup])
    out = check(ref(f"Wakefield AJ. {WAKEFIELD}. Lancet. 1998. doi:10.1016/dup", "10.1016/dup"), lk, NO_REGISTRY)
    assert out.found == "found" and out.doi == "10.1016/dup"
    assert out.status == "retracted"
    assert all("10.1016/orig" in f.detail for f in out.flags)


def test_unregistered_doi_is_reported_with_what_was_searched_never_as_fabricated():
    lk = FakeLookup(search=[item("10.1/x", "Something else entirely about maize", 2021)])
    text = "Smith J. Quantum entanglement improves maize. Nature Food. 2021. doi:10.1038/fake.1"
    out = check(ref(text, "10.1038/fake.1", 2021, "smith"), lk, NO_REGISTRY)
    assert out.found == "doi_not_registered"
    assert "Crossref bibliographic search" in out.searched
    assert any("not registered" in n for n in out.notes)


def test_journal_record_and_trailing_words_do_not_match_a_reference():
    venue = "Strojniski vestnik Journal of Mechanical Engineering"
    lk = FakeLookup(search=[item("10.5545/sv-jme", venue, None, type_="journal"),
                            item("10.1079/cabi", "Celastrus paniculatus", 2019, family="CABI")])
    text = f"Aryandari, C. (2023). Design and analysis of Celastrus paniculatus extracts. {venue}."
    assert check(ref(text, None, 2023, "aryandari"), lk, NO_REGISTRY).found == "not_found"


def test_ieee_notice_of_retraction_title_is_the_paper_itself():
    title = "Notice of Retraction: A tribological ANN-model of triphenyl phosphate acting on rapeseed oil"
    lk = FakeLookup(search=[item("10.1109/icnc.1", title, 2010, family="Liu")])
    out = check(ref(f"Liu, H. (2010). {title}. ICNC.", None, 2010, "liu"), lk, NO_REGISTRY)
    assert out.found == "found" and out.status == "retracted"


def test_notice_that_reuses_the_paper_title_is_not_counted_as_a_twin():
    title = "Hydroxychloroquine or chloroquine for COVID-19 registry analysis"
    paper = item("10.1016/paper", title, 2020, family="Mehra", **{"updated-by": [RETRACTION]})
    notice = item("10.1016/notice", title, 2020, family="Mehra")
    lk = FakeLookup(search=[paper, notice])
    out = check(ref(f"Mehra, M. (2020). {title}. Lancet.", None, 2020, "mehra"), lk, NO_REGISTRY)
    assert out.doi == "10.1016/paper"
    assert not any("other DOI" in f.detail for f in out.flags)


def test_near_miss_needs_the_author_to_match():
    # Title similarity 87: below the 90 that is accepted alone, so the surname decides.
    real = item("10.1680/ener.1", "Quantifying the impacts of wind variability", 2009, family="Milborrow")
    text = "Milborrow, D. (2008). Qunatifying the of wind variability. Proceedings of the ICE - Energy."
    assert check(ref(text, None, 2008, "milborrow"), FakeLookup(search=[real]), NO_REGISTRY).found == "found"
    assert check(ref(text, None, 2008, "smith"), FakeLookup(search=[real]), NO_REGISTRY).found == "not_found"
