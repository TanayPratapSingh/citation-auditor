from auditor.parse import (
    expand,
    extract_doi,
    find_citations,
    split_references,
    split_sections,
    strip_markers,
    title_segment,
)

DRAFT = """Statins lower LDL cholesterol [1, 3-4]. MMR vaccination was linked to autism (Wakefield et al., 1998; Smith 2020).
Jones and Lee (2019) found nothing. Si et al. (2026) disagreed. Unmatched claim [9].

References
1. Baigent C, Keech A. Efficacy and safety of cholesterol-lowering treatment. Lancet. 2005;366:1267-78. doi:10.1016/S0140-6736(05)67394-1.
2. Wakefield, A. J., Murch, S. H. (1998). Ileal-lymphoid-nodular hyperplasia. The Lancet, 351(9103), 637-641.
3. Smith, J. (2020). A paper about things. Journal, 12.
4. Jones, K. and Lee, M. (2019). Another paper about things.
5. Singh, T. P. (2026). afterlife. OSF.
6. Si, L., Salami, M. O. (2026). Tracking data quality.
"""


def refs():
    return split_references(split_sections(DRAFT)[1])


def test_numbered_references_and_fields():
    r = refs()
    assert [x.number for x in r] == [1, 2, 3, 4, 5, 6]
    assert r[0].doi == "10.1016/s0140-6736(05)67394-1"  # balanced ")" kept, trailing "." dropped
    assert (r[1].surname, r[1].year) == ("wakefield", 1998)


def test_citations_link_numeric_author_year_and_narrative():
    body = split_sections(DRAFT)[0]
    cites = {c.sentence.split()[0]: c for c in find_citations(body, refs())}
    assert cites["Statins"].refs == [1, 3, 4]
    assert cites["MMR"].refs == [2, 3]
    assert cites["Jones"].refs == [4]
    assert cites["Si"].refs == [6]  # whole word: "Si" must not match "Singh"
    assert cites["Unmatched"].unmatched == ["[9]"]


def test_bullets_page_numbers_and_line_break_hyphens():
    text = (
        "• Hauschke, C. (2025). Retracted papers in Infor-\nmation Science. J, 1.\n11\n"
        "• Ortega, J. (2024). Coverage. https://doi.org/10.1007/s11192-\n024-05034-y\n"
    )
    r = split_references(text)
    assert len(r) == 2
    assert "Information Science" in r[0].text and "11" not in r[0].text
    assert r[1].doi == "10.1007/s11192-024-05034-y"


def test_small_helpers():
    assert expand("1, 3-5") == [1, 3, 4, 5]
    assert extract_doi("see arXiv:1810.04805") == "10.48550/arxiv.1810.04805"
    assert strip_markers("Schneider et al. (2023) found 3% [2].") == "They found 3%."
    assert title_segment("Smith, J. (2020). A title with words. Journal.") == "A title with words"
    assert title_segment('J. Devlin, "BERT: Pre-training of deep models," in Proc. NAACL, 2019.') == (
        "BERT: Pre-training of deep models"
    )
    assert title_segment("Baigent C, Keech A. Efficacy and safety of treatment. Lancet. 2005.") == (
        "Efficacy and safety of treatment"
    )
