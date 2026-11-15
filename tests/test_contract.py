"""Contract tests for output/*.json (plan s3).

To check: grid/<band>.json, showcase.json, validation.json, earth_library.json
exist with the required keys; bands, rock, ground and matcher values are in the
fixed vocabularies; hardness is 0-3; placeholder outputs carry "fake": true.
"""


def test_placeholder():
    assert True
