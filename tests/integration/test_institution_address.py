"""Full postal address and postcode for every institution (ADR-013), with Abia's rules from its
structure file: address line 1, town and a 6-digit NIPOST postcode are required."""

from syntegra.core.context import RequestContext, use_context
from syntegra.governance import api as governance
from syntegra.governance.structure import Structure, load_structure
from syntegra.institutions import api as inst
from syntegra.institutions.domain import AddressRules, Placement, compose_address, validate_row
from tests.integration.test_tertiary_register import _abia

HEADER = "name,type,lga,levels,address_line1,address_line2,town,postcode\n"


async def _preview(sm, tenant, scope, csv_text):
    async with sm() as s, s.begin():
        with use_context(RequestContext(tenant.id, scope, None, system=True)):
            job, rows = await inst.get_job(
                s, await inst.preview_import(s, csv_text, source_name=None)
            )
            return job, rows, s


async def test_abia_requires_line1_town_and_a_six_digit_postcode(sm, world):
    tenant, bodies = await _abia(sm, world)
    csv_text = HEADER + (
        "TEST Complete School,,Ohafia,Primary,12 Market Road,Off Amaekpu Junction,Ohafia,440001\n"
        "TEST No Postcode,,Ohafia,Primary,12 Market Road,,Ohafia,\n"
        "TEST Bad Postcode,,Ohafia,Primary,12 Market Road,,Ohafia,44-0A1\n"
        "TEST No Street Or Town,,Ohafia,Primary,,,,440001\n"
        "TEST Spaced Postcode,,Ohafia,Primary,12 Market Road,,Ohafia,440 001\n"
    )
    _, rows, _ = await _preview(sm, tenant, bodies["ASUBEB"], csv_text)
    assert [r.status for r in rows] == ["valid", "error", "error", "error", "valid"]
    assert rows[4].data["postcode"] == "440001"  # typed with a space, stored as the 6 digits
    assert rows[1].messages == ["postcode is required"]
    assert rows[2].messages == ["postcode '44-0A1' is not in the expected format, e.g. 440001"]
    assert rows[3].messages == ["address line 1 is required", "town is required"]


async def test_registered_institutions_carry_the_full_address(sm, world):
    tenant, bodies = await _abia(sm, world)
    async with sm() as s, s.begin():
        with use_context(
            RequestContext(tenant.id, bodies["MIN-TERTIARY-EDUCATION"], None, system=True)
        ):
            job = await inst.preview_import(
                s,
                HEADER
                + (
                    "TEST Polytechnic,Polytechnic,Aba South,ND/HND,"
                    "2 Polytechnic Road,PMB 7166,Aba,450001\n"
                ),
                source_name=None,
            )
            await inst.commit_import(s, job, managing_body="Ministry")
            (poly,) = await inst.list_institutions(s)
            assert (poly.address_line1, poly.address_line2, poly.town, poly.postcode) == (
                "2 Polytechnic Road",
                "PMB 7166",
                "Aba",
                "450001",
            )
            assert poly.full_address == (
                "2 Polytechnic Road, PMB 7166, Aba, Aba South LGA, Abia State 450001, Nigeria"
            )


async def test_spreadsheet_header_variants_are_understood(sm, world):
    tenant, bodies = await _abia(sm, world)
    csv_text = (
        "Name,LGA,Levels,Address,City,Postal Code\n"
        "TEST Variant School,Ohafia,Primary,3 Road,Ohafia,440001\n"
    )
    job, rows, _ = await _preview(sm, tenant, bodies["ASUBEB"], csv_text)
    assert job.summary["valid"] == 1, rows[0].messages
    assert rows[0].data["address_line1"] == "3 Road" and rows[0].data["postcode"] == "440001"


async def test_another_country_brings_its_own_postcode_rules(sm, world):
    """P4: the UK format is data in its structure file; nothing in code is Nigerian."""
    uk = Structure.model_validate(
        {
            "version": 1,
            "country": {
                "code": "GB",
                "name": "United Kingdom",
                "time_zone": "Europe/London",
                "address": {
                    "postcode_label": "postcode",
                    "postcode_pattern": "^[A-Z]{1,2}[0-9][0-9A-Z]? [0-9][A-Z]{2}$",
                    "postcode_example": "SW1A 1AA",
                },
            },
            "address_required": ["postcode"],
            "bodies": [
                {
                    "code": "LA-EXAMPLE",
                    "name": "Example Local Authority",
                    "label": "Local authority",
                    "registers": ["primary"],
                }
            ],
        }
    )
    async with sm() as s, s.begin():
        tenant, bodies, _ = await governance.apply_structure(
            s, uk, platform_scope_id=world.global_root, residency_region="eu-west-2"
        )
    _, rows, _ = await _preview(
        sm,
        tenant,
        bodies["LA-EXAMPLE"],
        HEADER
        + (
            "TEST UK School,,Westminster,Primary,1 Road,,London,sw1a 1aa\n"
            "TEST UK School Two,,Westminster,Primary,1 Road,,London,440001\n"
        ),
    )
    assert rows[0].status == "valid" and rows[0].data["postcode"] == "SW1A 1AA"
    assert rows[1].messages == ["postcode '440001' is not in the expected format, e.g. SW1A 1AA"]


def test_compose_address_and_no_rules_means_nothing_required():
    assert compose_address(
        line1="12 Market Road",
        line2=None,
        town="Ohafia",
        location="Ohafia",
        postcode="440001",
        state="Abia State",
        country="Nigeria",
    ) == ("12 Market Road, Ohafia, Abia State 440001, Nigeria")
    no_rules = validate_row(
        2,
        {"name": "TEST School", "lga": "X", "levels": "Primary"},
        Placement("body", {}, {}),
        address=AddressRules(),
    )
    assert no_rules.clean is not None  # a jurisdiction without address rules requires nothing


async def test_abia_structure_records_its_address_rules(sm, world):
    tenant, _ = await _abia(sm, world)
    async with sm() as s:
        rules = await governance.resolve_config(s, tenant.root_scope_node_id, "address.rules")
    assert rules["required"] == ["address_line1", "town", "postcode"]
    assert rules["postcode_pattern"] == "^[0-9]{6}$" and rules["state"] == "Abia State"
    assert load_structure("ng-abia").country.address.postcode_example == "440001"
