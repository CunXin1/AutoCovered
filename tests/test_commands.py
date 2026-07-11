"""手机命令严格解析测试:宽松解析会把改价静默吞掉,严格性是安全要求。"""
from src.watcher import parse_command


def test_approve_plain():
    assert parse_command("APPROVE NVDA-abc123") == ("APPROVE", ("NVDA-abc123", None))


def test_approve_with_price_override():
    assert parse_command("APPROVE NVDA-abc123 @2.45") == ("APPROVE", ("NVDA-abc123", "2.45"))


def test_approve_case_insensitive_and_whitespace():
    assert parse_command("  approve   nvda-abc123   @2.4 ") == (
        "APPROVE", ("nvda-abc123", "2.4"))


def test_approve_price_without_at_rejected():
    """没有 @ 前缀的尾随数字必须拒识,不能静默丢弃或误解析。"""
    assert parse_command("APPROVE NVDA-abc123 2.45") is None


def test_approve_extra_tokens_rejected():
    assert parse_command("APPROVE NVDA-abc123 @2.45 please") is None


def test_approve_three_decimals_rejected():
    assert parse_command("APPROVE NVDA-abc123 @2.455") is None


def test_approve_missing_id_rejected():
    assert parse_command("APPROVE") is None


def test_reject():
    assert parse_command("REJECT NVDA-abc123") == ("REJECT", ("NVDA-abc123",))
    assert parse_command("REJECT a b") is None


def test_status_strict():
    assert parse_command("STATUS") == ("STATUS", ())
    assert parse_command("STATUS now") is None


def test_analyze_ticker_formats():
    assert parse_command("ANALYZE NVDA") == ("ANALYZE", ("NVDA",))
    assert parse_command("analyze brk.b") == ("ANALYZE", ("brk.b",))
    assert parse_command("ANALYZE 123456789012") is None


def test_confirm():
    assert parse_command("CONFIRM 12 @1.28") == ("CONFIRM", ("12", "1.28"))
    assert parse_command("CONFIRM 12 1.28") is None      # @ 必须有
    assert parse_command("CONFIRM abc @1.28") is None    # trade_id 必须是数字


def test_garbage_rejected():
    assert parse_command("买入 NVDA") is None
    assert parse_command("") is None
