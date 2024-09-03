from core.classifier import DeMarqueClassifier


class MockSubject:
    def __init__(self, identifier, name):
        self.identifier = identifier
        self.name = name


class TestDeMarqueClassifier:
    def _subject(self, identifier, name):
        subject = MockSubject(identifier, name)
        (
            subject.genre,
            subject.audience,
            subject.target_age,
            subject.fiction,
        ) = DeMarqueClassifier.classify(subject)
        return subject

    def test_scrub_identifier(self):
        """Make sure that the identifier matches with De Marque codes."""
        assert "READ0000" == DeMarqueClassifier.scrub_identifier("READ0000")

        # Otherwise, the identifier is left alone.
        assert "RRRR0000" != DeMarqueClassifier.scrub_identifier("RRRR0000")

    def test_scrub_name(self):
        """Sometimes a data provider sends BISAC names that contain extra or
        nonstandard characters. We store the data as it was provided to us,
        but when it's time to classify things, we normalize it.
        """
        assert "Early childhood" == DeMarqueClassifier.scrub_name("Early childhood")

    def test_audience(self):
        """Test that the correct audience is returned for each identifier."""
        assert "Children" == DeMarqueClassifier.audience("READ0001", "Early childhood")
        assert "Children" == DeMarqueClassifier.audience("READ0002", "Beginner reader")
        assert "Children" == DeMarqueClassifier.audience("READ0003", "Advanced reader")
        assert "Young Adult" == DeMarqueClassifier.audience("READ0004", "Teen")
        assert "Young Adult" == DeMarqueClassifier.audience("READ0005", "Young adult")
        assert "Adult" == DeMarqueClassifier.audience("READ0000", "Adult")

    def test_target_age(self):
        """Test that the correct target age range is returned for each identifier. Adult books do not have a target age
        range."""
        assert (0, 3) == DeMarqueClassifier.target_age("READ0001", "Early childhood")
        assert (4, 7) == DeMarqueClassifier.target_age("READ0002", "Beginner reader")
        assert (8, 12) == DeMarqueClassifier.target_age("READ0003", "Advanced reader")
        assert (13, 18) == DeMarqueClassifier.target_age("READ0004", "Teen")
        assert (17, None) == DeMarqueClassifier.target_age("READ0005", "Young Adult")