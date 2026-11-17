from db.time_parser import load_periods, parse_time


def test_basic_examples():
    assert parse_time("목3금12") == ([("THU", 3), ("FRI", 1), ("FRI", 2)], None)
    assert parse_time("월12수1") == ([("MON", 1), ("MON", 2), ("WED", 1)], None)
    assert parse_time("화910") == ([("TUE", 9), ("TUE", 10)], None)
    assert parse_time("월10") == ([("MON", 10)], None)


def test_period_zero_and_blank_time():
    assert parse_time("월0") == ([("MON", 0)], None)
    assert parse_time("-") == ([], None)
    assert parse_time("") == ([], None)
    assert parse_time("  -  ") == ([], None)


def test_ten_is_not_period_one_then_zero():
    assert parse_time("월110") == ([("MON", 1), ("MON", 10)], None)
    slots, error = parse_time("월101")
    assert slots == []
    assert error is not None
    assert "오름차순" in error


def test_periods_must_ascend_inside_a_day():
    slots, error = parse_time("월21")
    assert slots == []
    assert error is not None
    assert "오름차순" in error


def test_day_without_period_and_period_without_day():
    slots, error = parse_time("월")
    assert slots == []
    assert "교시가 없음" in error

    slots, error = parse_time("12")
    assert slots == []
    assert "요일 없이" in error


def test_unknown_character():
    slots, error = parse_time("월1 2")
    assert slots == []
    assert "허용되지 않는 문자" in error


def test_periods_config_covers_zero_through_ten():
    data = load_periods()
    assert data["period_minutes"] == 50
    assert set(data["periods"]) == {str(period) for period in range(11)}
