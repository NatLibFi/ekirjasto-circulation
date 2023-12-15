from pytest import register_assert_rewrite

register_assert_rewrite("tests.fixtures.database")
register_assert_rewrite("tests.fixtures.files")
register_assert_rewrite("tests.fixtures.vendor_id")

pytest_plugins = [
    "tests.fixtures.api_controller",
    "tests.fixtures.database",
]
