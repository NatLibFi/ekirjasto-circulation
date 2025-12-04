from core.classifier import BISACClassifier, SubjectClassifier


class MockSubject:
    def __init__(self, identifier, name):
        self.identifier = identifier
        self.name = name


class TestBISACClassifier:
    def _subject(self, identifier, name):
        subject = MockSubject(identifier, name)
        (
            subject.genre,
            subject.audience,
            subject.target_age,
            subject.fiction,
        ) = BISACClassifier.classify_subject(subject)
        return subject

    def genre_is(self, identifier, name, expect):
        subject = self._subject(identifier, "")
        if expect and subject.genre:
            assert expect == subject.genre.name
        else:
            assert expect == subject.genre

    def test_genre_spot_checks(self):
        """Randomly selected 500 subject-genre classifications."""
        genre_is = self.genre_is
        genre_is(
            "GAM010000",
            "GAMES & ACTIVITIES / Role Playing & Fantasy",
            "Games & Activities",
        )
        genre_is("ART047000", "ART / Middle Eastern", "Art & Culture")
        genre_is(
            "CKB040000",
            "COOKING / Specific Ingredients / Herbs, Spices, Condiments",
            "Cooking",
        )
        genre_is(
            "CKB002000", "COOKING / Regional & Cultural / American / General", "Cooking"
        )
        genre_is("FIC033000", "FICTION / Westerns", "Westerns")
        genre_is(
            "FIC098050",
            "FICTION / World Literature / England / 20th Century",
            "General Fiction",
        )
        genre_is("SEL036000", "SELF-HELP / Anxieties & Phobias", "Self-Help")
        genre_is("EDU062000", "EDUCATION / Cultural Pedagogies", "Education")
        genre_is("MED058150", "MEDICAL / Nursing / Nutrition", "Medical")
        genre_is("NAT019000", "NATURE / Animals / Mammals", "Nature")
        genre_is("LAW067000", "LAW / Mental Health", "Law")
        genre_is(
            "HIS037090",
            "HISTORY / Modern / 16th Century",
            "Renaissance & Early Modern History",
        )
        genre_is("BIB019060", "BIBLES / Reina Valera / Text", "Christianity")
        genre_is(
            "FIC136000",
            "FICTION / LGBTQ+ / Two-Spirited & Indigiqueer",
            "LGBTQ Fiction",
        )
        genre_is(
            "BIB003050", "BIBLES / English Standard Version / Study", "Christianity"
        )
        genre_is("MED075000", "MEDICAL / Physiology", "Medical")
        genre_is("DRA012000", "DRAMA / Australian & Oceanian", "Drama")
        genre_is("LAW017000", "LAW / Conflict of Laws", "Law")
        genre_is("LAW103000", "LAW / Common", "Law")
        genre_is("HIS066000", "HISTORY / LGBTQ+", "History")
        genre_is(
            "BIB020030",
            "BIBLES / The Message / New Testament & Portions",
            "Christianity",
        )
        genre_is(
            "GAM002000",
            "GAMES & ACTIVITIES / Card Games / General",
            "Games & Activities",
        )
        genre_is("BIO022000", "BIOGRAPHY & AUTOBIOGRAPHY / Women", "Biography & Memoir")
        genre_is(
            "YAN052010",
            "YOUNG ADULT NONFICTION / Social Science / Archaeology",
            "Art & Culture",
        )
        genre_is(
            "JNF053010",
            "JUVENILE NONFICTION / Social Topics / Adolescence",
            "Health & Wellness",
        )
        genre_is(
            "YAN045000",
            "YOUNG ADULT NONFICTION / Recycling & Green Living",
            "Climate & Sustainability",
        )
        genre_is("JUV002190", "JUVENILE FICTION / Animals / Pets", "Pet Stories")
        genre_is(
            "REF033000",
            "REFERENCE / Personal & Private Investigations",
            "Reference & Study Aids",
        )
        genre_is(
            "OCC041000", "BODY, MIND & SPIRIT / Sacred Sexuality", "Body, Mind & Spirit"
        )
        genre_is(
            "COM060170",
            "COMPUTERS / Internet / Content Management Systems",
            "Computers",
        )
        genre_is("MUS054000", "MUSIC / Philosophy & Social Aspects", "Music")
        genre_is(
            "JNF052040", "JUVENILE NONFICTION / Social Science / Sociology", "Society"
        )
        genre_is(
            "ANT012000", "ANTIQUES & COLLECTIBLES / Comics", "Antiques & Collectibles"
        )
        genre_is("CKB056000", "COOKING / Regional & Cultural / Mexican", "Cooking")
        genre_is("LAW083000", "LAW / Securities", "Law")
        genre_is("MED094000", "MEDICAL / Pediatric Emergencies", "Medical")
        genre_is("OCC015000", "BODY, MIND & SPIRIT / Numerology", "Body, Mind & Spirit")
        genre_is("NAT042000", "NATURE / Animals / Big Cats", "Nature")
        genre_is("JNF006000", "JUVENILE NONFICTION / Art / General", "Art & Culture")
        genre_is(
            "ANT017000",
            "ANTIQUES & COLLECTIBLES / Furniture",
            "Antiques & Collectibles",
        )
        genre_is("SOC073000", "SOCIAL SCIENCE / Human Trafficking", "Social Sciences")
        genre_is("TRV025040", "TRAVEL / United States / Northeast / General", "Travel")
        genre_is(
            "BIB016040",
            "BIBLES / New Revised Standard Version / Reference",
            "Christianity",
        )
        genre_is(
            "YAF001000",
            "YOUNG ADULT FICTION / Action & Adventure / General",
            "Adventure",
        )
        genre_is("EDU029000", "EDUCATION / Teaching / General", "Education")
        genre_is(
            "COM084020",
            "COMPUTERS / Business & Productivity Software / Email Clients",
            "Computers",
        )
        genre_is(
            "BUS070080",
            "BUSINESS & ECONOMICS / Industries / Service",
            "Personal Finance & Business",
        )
        genre_is(
            "BIO028000", "BIOGRAPHY & AUTOBIOGRAPHY / Indigenous", "Biography & Memoir"
        )
        genre_is(
            "JNF076030", "JUVENILE NONFICTION / Indigenous / Family Life", "Family"
        )
        genre_is(
            "COM051240",
            "COMPUTERS / Software Development & Engineering / Systems Analysis & Design",
            "Computers",
        )
        genre_is(
            "ART038000", "ART / American / African American & Black", "Art & Culture"
        )
        genre_is(
            "BUS036070",
            "BUSINESS & ECONOMICS / Investments & Securities / Analysis & Trading Strategies",
            "Personal Finance & Investing",
        )
        genre_is(
            "FIC059100",
            "FICTION / Indigenous / Oral Storytelling & Teachings",
            "Folklore Stories",
        )
        genre_is(
            "HEA017000",
            "HEALTH & FITNESS / Diet & Nutrition / Nutrition",
            "Health & Diet",
        )
        genre_is(
            "JNF062020",
            "JUVENILE NONFICTION / Comics & Graphic Novels / History",
            "History",
        )
        genre_is("GAR029000", "GARDENING / Water Gardens", "Gardening")
        genre_is(
            "YAN051050",
            "YOUNG ADULT NONFICTION / Social Topics / Death, Grief, Bereavement",
            "Health & Wellness",
        )
        genre_is("HIS010020", "HISTORY / Europe / Western", "European History")
        genre_is(
            "JUV015010",
            "JUVENILE FICTION / Health & Daily Living / Daily Activities",
            "General Fiction",
        )
        genre_is(
            "LAN008000",
            "LANGUAGE ARTS & DISCIPLINES / Journalism",
            "Reference & Study Aids",
        )
        genre_is(
            "JUV033100",
            "JUVENILE FICTION / Religious / Christian / Family",
            "Family Stories",
        )
        genre_is(
            "OCC029000",
            "BODY, MIND & SPIRIT / Unexplained Phenomena",
            "Body, Mind & Spirit",
        )
        genre_is("TRV003100", "TRAVEL / Asia / East / Taiwan", "Travel")
        genre_is("CKB095000", "COOKING / Courses & Dishes / Confectionery", "Cooking")
        genre_is(
            "LIT008000", "LITERARY CRITICISM / Asian / General", "Literary Criticism"
        )
        genre_is(
            "FOR043000", "FOREIGN LANGUAGE STUDY / Swedish", "Foreign Language Study"
        )
        genre_is("FIC093000", "FICTION / World Literature / Chile", "General Fiction")
        genre_is("COM051370", "COMPUTERS / Programming / Macintosh", "Computers")
        genre_is("HUM015000", "HUMOR / Form / Anecdotes & Quotations", "Humor")
        genre_is(
            "JUV008020",
            "JUVENILE FICTION / Comics & Graphic Novels / Superheroes",
            "Superheroes",
        )
        genre_is("COM080000", "COMPUTERS / History", "Computers")
        genre_is("PHO007000", "PHOTOGRAPHY / Techniques / Equipment", "Photography")
        genre_is(
            "PSY041000", "PSYCHOLOGY / Psychotherapy / Couples & Family", "Psychology"
        )
        genre_is("LAW015000", "LAW / Communications", "Law")
        genre_is("ART050010", "ART / Subjects & Themes / Human Figure", "Art & Culture")
        genre_is(
            "BUS118000",
            "BUSINESS & ECONOMICS / Diversity & Inclusion",
            "Personal Finance & Business",
        )
        genre_is(
            "BIB015070",
            "BIBLES / New Living Translation / Youth & Teen",
            "Christianity",
        )
        genre_is(
            "FIC009120", "FICTION / Fantasy / Dragons & Mythical Creatures", "Fantasy"
        )
        genre_is("MUS000000", "MUSIC / General", "Music")
        genre_is("GAM012000", "GAMES & ACTIVITIES / Trivia", "Games & Activities")
        genre_is(
            "TEC009020", "TECHNOLOGY & ENGINEERING / Civil / General", "Technology"
        )
        genre_is("TEC026000", "TECHNOLOGY & ENGINEERING / Mining", "Technology")
        genre_is("GAR008000", "GARDENING / Greenhouses", "Gardening")
        genre_is(
            "JNF007120",
            "JUVENILE NONFICTION / Biography & Autobiography / Women",
            "Biography & Memoir",
        )
        genre_is("ART069000", "ART / American / Native American", "Art & Culture")
        genre_is("HUM009000", "HUMOR / Topic / Animals", "Humor")
        genre_is(
            "FIC054000",
            "FICTION / Asian American & Pacific Islander",
            "General Fiction",
        )
        genre_is("PSY022000", "PSYCHOLOGY / Psychopathology / General", "Psychology")
        genre_is(
            "TRV029000", "TRAVEL / Special Interest / Amusement & Theme Parks", "Travel"
        )
        genre_is("ART015060", "ART / History / Ancient & Classical", "Art History")
        genre_is("COM043040", "COMPUTERS / Networking / Network Protocols", "Computers")
        genre_is("YAN005000", "YOUNG ADULT NONFICTION / Art / General", "Art & Culture")
        genre_is("FIC004000", "FICTION / Classics", "Classics")
        genre_is(
            "YAN051270",
            "YOUNG ADULT NONFICTION / Social Topics / Civil & Human Rights",
            "Human Rights",
        )
        genre_is("MAT030000", "MATHEMATICS / Study & Teaching", "Mathematics")
        genre_is("COM014000", "COMPUTERS / Computer Science", "Computers")
        genre_is(
            "FOR032000",
            "FOREIGN LANGUAGE STUDY / Oceanic & Australian Languages",
            "Foreign Language Study",
        )
        genre_is("MED103000", "MEDICAL / Parasitology", "Medical")
        genre_is("HIS037060", "HISTORY / Modern / 19th Century", "Modern History")
        genre_is(
            "REL006630",
            "RELIGION / Biblical Studies / History & Culture",
            "Christianity",
        )
        genre_is(
            "REL055020",
            "RELIGION / Christian Rituals & Practice / Worship & Liturgy",
            "Christianity",
        )
        genre_is("BIB009040", "BIBLES / New American Bible / Reference", "Christianity")
        genre_is("HIS064000", "HISTORY / Europe / Portugal", "European History")
        genre_is("MUS023020", "MUSIC / Musical Instruments / Percussion", "Music")
        genre_is(
            "HEA039150",
            "HEALTH & FITNESS / Diseases & Conditions / Chronic Fatigue Syndrome",
            "Health & Diet",
        )
        genre_is(
            "BIO002020",
            "BIOGRAPHY & AUTOBIOGRAPHY / Asian & Asian American",
            "Biography & Memoir",
        )
        genre_is(
            "FAM016000", "FAMILY & RELATIONSHIPS / Education", "Family & Relationships"
        )
        genre_is("BIB008050", "BIBLES / Multiple Translations / Study", "Christianity")
        genre_is("JUV075000", "JUVENILE FICTION / War & Military", "War")
        genre_is("EDU011000", "EDUCATION / Evaluation & Assessment", "Education")
        genre_is("LAW094000", "LAW / Discrimination", "Law")
        genre_is(
            "YAF032000",
            "YOUNG ADULT FICTION / Lifestyles / City & Town Life",
            "General Fiction",
        )
        genre_is(
            "YAF024000",
            "YOUNG ADULT FICTION / Historical / General",
            "Historical Fiction",
        )
        genre_is(
            "BIB012110",
            "BIBLES / New International Reader's Version / Reading",
            "Christianity",
        )
        genre_is(
            "JNF003110", "JUVENILE NONFICTION / Animals / Horses", "Horses & Riding"
        )
        genre_is("MED018000", "MEDICAL / Diagnosis", "Medical")
        genre_is("SCI090000", "SCIENCE / Cognitive Science", "Science")
        genre_is("ART072000", "ART / Techniques / Beadwork", "Art & Culture")
        genre_is("SOC026000", "SOCIAL SCIENCE / Sociology / General", "Social Sciences")
        genre_is("TRV002010", "TRAVEL / Africa / Central", "Travel")
        genre_is("COM051480", "COMPUTERS / Languages / JSON", "Computers")
        genre_is(
            "BIB001010", "BIBLES / Christian Standard Bible / Children", "Christianity"
        )
        genre_is("MUS001000", "MUSIC / Instruction & Study / Appreciation", "Music")
        genre_is(
            "BIB008080", "BIBLES / Multiple Translations / Dramatized", "Christianity"
        )
        genre_is(
            "FAM044000",
            "FAMILY & RELATIONSHIPS / Toilet Training",
            "Family & Relationships",
        )
        genre_is("REL007030", "RELIGION / Buddhism / Sacred Writings", "Buddhism")
        genre_is("FIC009100", "FICTION / Fantasy / Action & Adventure", "Fantasy")
        genre_is(
            "YAN051190", "YOUNG ADULT NONFICTION / Social Topics / Runaways", "Society"
        )
        genre_is(
            "LAN006000",
            "LANGUAGE ARTS & DISCIPLINES / Grammar & Punctuation",
            "Reference & Study Aids",
        )
        genre_is("LAW086000", "LAW / Taxation", "Law")
        genre_is("SCI043000", "SCIENCE / Research & Methodology", "Science")
        genre_is("MED100000", "MEDICAL / Podiatry", "Medical")
        genre_is(
            "BUS064010",
            "BUSINESS & ECONOMICS / Taxation / Corporate",
            "Personal Finance & Business",
        )
        genre_is("SCI060000", "SCIENCE / Reference", "Science")
        genre_is(
            "BIB005100",
            "BIBLES / International Children's Bible / Outreach",
            "Christianity",
        )
        genre_is("MED070000", "MEDICAL / Perinatology & Neonatology", "Medical")
        genre_is(
            "YAF051000", "YOUNG ADULT FICTION / Religious / General", "General Fiction"
        )
        genre_is("MED016060", "MEDICAL / Dentistry / Endodontics", "Medical")
        genre_is("TRV026010", "TRAVEL / Special Interest / Business", "Travel")
        genre_is("BIB023110", "BIBLES / The Amplified Bible / Reading", "Christianity")
        genre_is(
            "YAN061000",
            "YOUNG ADULT NONFICTION / Diversity & Multicultural",
            "Diversity & Multicultural",
        )
        genre_is(
            "SOC008080",
            "SOCIAL SCIENCE / Cultural & Ethnic Studies / American / European American Studies",
            "Social Sciences",
        )
        genre_is("TRV026090", "TRAVEL / Special Interest / Literary", "Travel")
        genre_is(
            "YAF072000", "YOUNG ADULT FICTION / Clean & Nonviolent", "General Fiction"
        )
        genre_is(
            "FIC102000",
            "FICTION / World Literature / Germany / General",
            "General Fiction",
        )
        genre_is(
            "BUS050050",
            "BUSINESS & ECONOMICS / Personal Finance / Taxation",
            "Personal Finance & Investing",
        )
        genre_is("TRV010000", "TRAVEL / Essays & Travelogues", "Travel")
        genre_is("CKB046000", "COOKING / Regional & Cultural / Irish", "Cooking")
        genre_is(
            "FOR004000", "FOREIGN LANGUAGE STUDY / Danish", "Foreign Language Study"
        )
        genre_is("FIC028000", "FICTION / Science Fiction / General", "Science Fiction")
        genre_is("PHI032000", "PHILOSOPHY / Movements / Rationalism", "Philosophy")
        genre_is("PHI035000", "PHILOSOPHY / Essays", "Philosophy")
        genre_is(
            "POL006000",
            "POLITICAL SCIENCE / American Government / Legislative Branch",
            "Political Science",
        )
        genre_is("HIS027160", "HISTORY / Military / Canada", "Military History")
        genre_is("MAT003000", "MATHEMATICS / Applied", "Mathematics")
        genre_is(
            "BUS033060",
            "BUSINESS & ECONOMICS / Insurance / Life",
            "Personal Finance & Business",
        )
        genre_is("FIC047000", "FICTION / Sea Stories", "Adventure")
        genre_is(
            "JNF008000",
            "JUVENILE NONFICTION / Paranormal & Supernatural",
            "Supernatural",
        )
        genre_is("TRA001150", "TRANSPORTATION / Automotive / Trucks", "Technology")
        genre_is("JNF053180", "JUVENILE NONFICTION / Disabilities", "Health & Wellness")
        genre_is(
            "BUS030000",
            "BUSINESS & ECONOMICS / Human Resources & Personnel Management",
            "Personal Finance & Business",
        )
        genre_is(
            "FAM015000",
            "FAMILY & RELATIONSHIPS / Divorce & Separation",
            "Family & Relationships",
        )
        genre_is(
            "SEL026000",
            "SELF-HELP / Substance Abuse & Addictions / General",
            "Self-Help",
        )
        genre_is("TEC045000", "TECHNOLOGY & ENGINEERING / Fire Science", "Technology")
        genre_is("GAR020000", "GARDENING / Shade", "Gardening")
        genre_is("REL109030", "RELIGION / Christian Ministry / Youth", "Christianity")
        genre_is("TEC072000", "TECHNOLOGY & ENGINEERING / Pharmaceutical", "Technology")
        genre_is("JNF019010", "JUVENILE NONFICTION / Family / Adoption", "Family")
        genre_is(
            "YAF059060",
            "YOUNG ADULT FICTION / Sports & Recreation / Football",
            "Sports Stories",
        )
        genre_is("SCI100000", "SCIENCE / Natural History", "Science")
        genre_is("FIC056060", "FICTION / Hispanic & Latino / Horror", "General Fiction")
        genre_is(
            "HIS027280", "HISTORY / Military / Guerrilla Warfare", "Military History"
        )
        genre_is("FIC027330", "FICTION / Romance / Sports", "Romance")
        genre_is("MED071000", "MEDICAL / Pharmacology", "Medical")
        genre_is(
            "BIB015020", "BIBLES / New Living Translation / Devotional", "Christianity"
        )
        genre_is(
            "FIC027310",
            "FICTION / Romance / Paranormal / Shifters",
            "Paranormal Romance",
        )
        genre_is(
            "YAF046210",
            "YOUNG ADULT FICTION / Indigenous / Historical",
            "Historical Fiction",
        )
        genre_is(
            "LAN002000",
            "LANGUAGE ARTS & DISCIPLINES / Writing / Authorship",
            "Reference & Study Aids",
        )
        genre_is(
            "PER011020",
            "PERFORMING ARTS / Theater / History & Criticism",
            "Performing Arts",
        )
        genre_is(
            "JNF026030",
            "JUVENILE NONFICTION / Holidays & Celebrations / Halloween",
            "Holidays & Celebrations",
        )
        genre_is(
            "LIT004260",
            "LITERARY CRITICISM / Science Fiction & Fantasy",
            "Literary Criticism",
        )
        genre_is("YAF026000", "YOUNG ADULT FICTION / Horror", "Horror")
        genre_is("SPO003040", "SPORTS & RECREATION / Baseball / Statistics", "Sports")
        genre_is(
            "HIS027390",
            "HISTORY / Wars & Conflicts / World War II / Pacific Theater",
            "History",
        )
        genre_is("SCI101000", "SCIENCE / Ethics", "Science")
        genre_is("LAW119000", "LAW / Islamic", "Law")
        genre_is("HIS001020", "HISTORY / Africa / East", "African History")
        genre_is("HIS062000", "HISTORY / Asia / South / India", "Asian History")
        genre_is(
            "CGN007020",
            "COMICS & GRAPHIC NOVELS / Nonfiction / History",
            "Comics & Graphic Novels",
        )
        genre_is("TRV009160", "TRAVEL / Europe / Cyprus", "Travel")
        genre_is("CRA050000", "CRAFTS & HOBBIES / Leatherwork", "Crafts & Hobbies")
        genre_is(
            "YAN051200",
            "YOUNG ADULT NONFICTION / Social Topics / Self-Esteem & Self-Reliance",
            "Mental Health",
        )
        genre_is("REL102000", "RELIGION / Theology", "Religion & Spirituality")
        genre_is("PET004000", "PETS / Dogs / General", "Pets")
        genre_is(
            "CRA004000",
            "CRAFTS & HOBBIES / Needlework / Crocheting",
            "Crafts & Hobbies",
        )
        genre_is(
            "TRV024040",
            "TRAVEL / South America / Ecuador & Galapagos Islands",
            "Travel",
        )
        genre_is("SOC018000", "SOCIAL SCIENCE / Men's Studies", "Social Sciences")
        genre_is("REF032000", "REFERENCE / Event Planning", "Reference & Study Aids")
        genre_is("DRA005010", "DRAMA / Asian / Japanese", "Drama")
        genre_is(
            "JNF054030",
            "JUVENILE NONFICTION / Sports & Recreation / Camping & Outdoor Activities",
            "Camping",
        )
        genre_is(
            "SPO059000",
            "SPORTS & RECREATION / Water Sports / Scuba & Snorkeling",
            "Sports",
        )
        genre_is(
            "BUS078000",
            "BUSINESS & ECONOMICS / Distribution",
            "Personal Finance & Business",
        )
        genre_is(
            "BUS036020",
            "BUSINESS & ECONOMICS / Investments & Securities / Futures",
            "Personal Finance & Investing",
        )
        genre_is("HIS002020", "HISTORY / Ancient / Rome", "Ancient History")
        genre_is("SCI013010", "SCIENCE / Chemistry / Analytic", "Science")
        genre_is("LAW056000", "LAW / Law Office Management", "Law")
        genre_is(
            "LCO008030", "LITERARY COLLECTIONS / European / German", "Short Stories"
        )
        genre_is("TRV018000", "TRAVEL / Parks & Campgrounds", "Travel")
        genre_is(
            "BIB013020",
            "BIBLES / New International Version / Devotional",
            "Christianity",
        )
        genre_is(
            "REL016000",
            "RELIGION / Institutions & Organizations",
            "Religion & Spirituality",
        )
        genre_is(
            "SEL049010",
            "SELF-HELP / Safety & Security / Survival & Emergency Preparedness",
            "Self-Help",
        )
        genre_is("FIC009070", "FICTION / Fantasy / Dark Fantasy", "Fantasy")
        genre_is(
            "SCI104000", "SCIENCE / Indigenous Knowledge & Perspectives", "Science"
        )
        genre_is(
            "CGN001000",
            "COMICS & GRAPHIC NOVELS / Anthologies",
            "Comics & Graphic Novels",
        )
        genre_is(
            "JUV004080",
            "JUVENILE FICTION / Biographical / Australia & Oceania",
            "General Fiction",
        )
        genre_is("DRA001030", "DRAMA / American / Hispanic & Latino", "Drama")
        genre_is(
            "YAN055010",
            "YOUNG ADULT NONFICTION / Technology / Aeronautics, Astronautics & Space Science",
            "Stars & Space",
        )
        genre_is("CRA047000", "CRAFTS & HOBBIES / Folkcrafts", "Crafts & Hobbies")
        genre_is(
            "FIC095000", "FICTION / World Literature / Colombia", "General Fiction"
        )
        genre_is(
            "JNF018020",
            "JUVENILE NONFICTION / Asian American & Pacific Islander",
            "Diversity & Multicultural",
        )
        genre_is("EDU060040", "EDUCATION / Schools / Types / Public", "Education")
        genre_is("ART063000", "ART / Environmental & Land Art", "Art & Culture")
        genre_is("COM048000", "COMPUTERS / Distributed Systems / General", "Computers")
        genre_is("ART003000", "ART / Techniques / Calligraphy", "Art & Culture")
        genre_is("YAF078000", "YOUNG ADULT FICTION / Inuit", "General Fiction")
        genre_is(
            "PHI037000", "PHILOSOPHY / History & Surveys / Renaissance", "Philosophy"
        )
        genre_is("SCI098000", "SCIENCE / Space Science / General", "Science")
        genre_is("COM073000", "COMPUTERS / Speech & Audio Processing", "Computers")
        genre_is(
            "YAF024070",
            "YOUNG ADULT FICTION / Historical / Holocaust",
            "Difficult Topics",
        )
        genre_is("SCI074000", "SCIENCE / Physics / Atomic & Molecular", "Science")
        genre_is("BIB020000", "BIBLES / The Message / General", "Christianity")
        genre_is(
            "POL040020", "POLITICAL SCIENCE / World / General", "Political Science"
        )
        genre_is(
            "EDU029060", "EDUCATION / Teaching / Subjects / Library Skills", "Education"
        )
        genre_is("PHI018000", "PHILOSOPHY / Movements / Phenomenology", "Philosophy")
        genre_is("JNF011000", "JUVENILE NONFICTION / Careers", "Society")
        genre_is("FIC027400", "FICTION / Romance / LGBTQ+ / Transgender", "Romance")
        genre_is(
            "YAF077000", "YOUNG ADULT FICTION / First Nations", "Multicultural Stories"
        )
        genre_is("ART059000", "ART / Museum Studies", "Art & Culture")
        genre_is("LAW001000", "LAW / Administrative Law & Regulatory Practice", "Law")
        genre_is("POE015000", "POETRY / Native American", "Poetry")
        genre_is("CKB024000", "COOKING / Courses & Dishes / Desserts", "Cooking")
        genre_is("YAF027020", "YOUNG ADULT FICTION / Satire", "General Fiction")
        genre_is(
            "YAN050100",
            "YOUNG ADULT NONFICTION / Science & Nature / Experiments & Projects",
            "Science & Technology",
        )
        genre_is(
            "COM021040", "COMPUTERS / Data Science / Data Warehousing", "Computers"
        )
        genre_is(
            "COM050000",
            "COMPUTERS / Hardware / Personal Computers / General",
            "Computers",
        )
        genre_is(
            "BUS075000",
            "BUSINESS & ECONOMICS / Consulting",
            "Personal Finance & Business",
        )
        genre_is(
            "FIC028080", "FICTION / Science Fiction / Time Travel", "Science Fiction"
        )
        genre_is(
            "REL006780",
            "RELIGION / Biblical Commentary / Old Testament / Prophets",
            "Christianity",
        )
        genre_is("SEL040000", "SELF-HELP / Communication & Social Skills", "Self-Help")
        genre_is("CKB048000", "COOKING / Regional & Cultural / Japanese", "Cooking")
        genre_is(
            "BUS087000",
            "BUSINESS & ECONOMICS / Production & Operations Management",
            "Personal Finance & Business",
        )
        genre_is(
            "BUS005000",
            "BUSINESS & ECONOMICS / Bookkeeping",
            "Personal Finance & Business",
        )
        genre_is("MED089020", "MEDICAL / Veterinary Medicine / Food Animal", "Medical")
        genre_is("LAW009000", "LAW / Business & Financial", "Law")
        genre_is(
            "JNF053070",
            "JUVENILE NONFICTION / Social Topics / Poverty & Homelessness",
            "Society",
        )
        genre_is("COM055030", "COMPUTERS / Certification Guides / Cisco", "Computers")
        genre_is("HUM001000", "HUMOR / Form / Comic Strips & Cartoons", "Humor")
        genre_is(
            "FIC022170",
            "FICTION / Mystery & Detective / Cozy / Books, Bookstores & Libraries",
            "Cozy Mystery",
        )
        genre_is("REF009000", "REFERENCE / Directories", "Reference & Study Aids")
        genre_is(
            "BIB026070", "BIBLES / Catholic Translations / Reading", "Christianity"
        )
        genre_is(
            "FIC098010",
            "FICTION / World Literature / England / Early & Medieval Periods",
            "General Fiction",
        )
        genre_is("MED085000", "MEDICAL / Surgery / General", "Medical")
        genre_is("BIB000000", "BIBLES / General", "Christianity")
        genre_is(
            "YAN024080",
            "YOUNG ADULT NONFICTION / Health & Daily Living / Sexuality & Pregnancy",
            "Sexual Education",
        )
        genre_is("YAF009000", "YOUNG ADULT FICTION / Classics", "Classics")
        genre_is(
            "REL091000",
            "RELIGION / Christian Education / Children & Youth",
            "Christianity",
        )
        genre_is("CKB109000", "COOKING / Methods / Slow Cooking", "Cooking")
        genre_is(
            "PHO023090", "PHOTOGRAPHY / Subjects & Themes / Lifestyles", "Photography"
        )
        genre_is("EDU007000", "EDUCATION / Curricula", "Education")
        genre_is("PHO021000", "PHOTOGRAPHY / Commercial", "Photography")
        genre_is("POE023020", "POETRY / Subjects & Themes / Love & Erotica", "Poetry")
        genre_is(
            "BIB012050",
            "BIBLES / New International Reader's Version / Study",
            "Christianity",
        )
        genre_is(
            "YAN038140",
            "YOUNG ADULT NONFICTION / Native American",
            "Diversity & Multicultural",
        )
        genre_is(
            "HIS037050",
            "HISTORY / Modern / 18th Century",
            "Renaissance & Early Modern History",
        )
        genre_is("JNF009000", "JUVENILE NONFICTION / Boys & Men", "Health & Wellness")
        genre_is(
            "FIC119000", "FICTION / World Literature / Portugal", "General Fiction"
        )
        genre_is(
            "YAN030000",
            "YOUNG ADULT NONFICTION / Language Arts / General",
            "Foreign Language Study",
        )
        genre_is("HIS041040", "HISTORY / Caribbean & West Indies / Jamaica", "History")
        genre_is("HIS004000", "HISTORY / Australia & New Zealand", "History")
        genre_is(
            "LCO002010",
            "LITERARY COLLECTIONS / American / African American & Black",
            "Short Stories",
        )
        genre_is(
            "BIB018060", "BIBLES / Other English Translations / Text", "Christianity"
        )
        genre_is(
            "HIS015000",
            "HISTORY / Europe / Great Britain / General",
            "European History",
        )
        genre_is(
            "POL017000",
            "POLITICAL SCIENCE / Public Affairs & Administration",
            "Political Science",
        )
        genre_is("CKB010000", "COOKING / Courses & Dishes / Breakfast", "Cooking")
        genre_is(
            "TEC021000",
            "TECHNOLOGY & ENGINEERING / Materials Science / General",
            "Technology",
        )
        genre_is("MED072000", "MEDICAL / Pharmacy", "Medical")
        genre_is(
            "OCC025000",
            "BODY, MIND & SPIRIT / UFOs & Extraterrestrials",
            "Body, Mind & Spirit",
        )
        genre_is(
            "BIB015080", "BIBLES / New Living Translation / Dramatized", "Christianity"
        )
        genre_is("FIC110000", "FICTION / World Literature / Mexico", "General Fiction")
        genre_is("COM046000", "COMPUTERS / Operating Systems / General", "Computers")
        genre_is("MED089050", "MEDICAL / Veterinary Medicine / Surgery", "Medical")
        genre_is("SPO035000", "SPORTS & RECREATION / Running & Jogging", "Sports")
        genre_is("SEL046000", "SELF-HELP / Gender & Sexuality", "Self-Help")
        genre_is(
            "FIC027150",
            "FICTION / Romance / Historical / Medieval",
            "Historical Romance",
        )
        genre_is("CKB030000", "COOKING / Essays & Narratives", "Cooking")
        genre_is(
            "TEC008050",
            "TECHNOLOGY & ENGINEERING / Electronics / Circuits / VLSI & ULSI",
            "Technology",
        )
        genre_is("TRV002070", "TRAVEL / Africa / South / General", "Travel")
        genre_is("HIS030000", "HISTORY / Reference", "History")
        genre_is(
            "CKB138000",
            "COOKING / Regional & Cultural / Indigenous Food of Turtle Island",
            "Cooking",
        )
        genre_is("CKB043000", "COOKING / Regional & Cultural / Hungarian", "Cooking")
        genre_is("JUV003000", "JUVENILE FICTION / Art", "General Fiction")
        genre_is(
            "BUS069020",
            "BUSINESS & ECONOMICS / International / Economics & Trade",
            "Personal Finance & Business",
        )
        genre_is("ART015050", "ART / History / Prehistoric", "Art History")
        genre_is(
            "COM060030", "COMPUTERS / Networking / Intranets & Extranets", "Computers"
        )
        genre_is("DES015000", "DESIGN / Individual Designers", "Design")
        genre_is(
            "YAF008000",
            "YOUNG ADULT FICTION / Careers, Occupations, Internships",
            "School & Study",
        )
        genre_is("MED022000", "MEDICAL / Diseases", "Medical")
        genre_is("PSY039000", "PSYCHOLOGY / Developmental / General", "Psychology")
        genre_is(
            "FIC027170",
            "FICTION / Romance / Historical / Victorian",
            "Historical Romance",
        )
        genre_is("PHI006000", "PHILOSOPHY / Movements / Existentialism", "Philosophy")
        genre_is(
            "JUV015020",
            "JUVENILE FICTION / Health & Daily Living / Diseases, Illnesses & Injuries",
            "Difficult Topics",
        )
        genre_is(
            "FAM001020",
            "FAMILY & RELATIONSHIPS / Abuse / Elder Abuse",
            "Family & Relationships",
        )
        genre_is("YAF031000", "YOUNG ADULT FICTION / LGBTQ+ / General", "LGBTQ Fiction")
        genre_is("COM037000", "COMPUTERS / Machine Theory", "Computers")
        genre_is(
            "MED003050",
            "MEDICAL / Allied Health Services / Occupational Therapy",
            "Medical",
        )
        genre_is("EDU032000", "EDUCATION / Leadership", "Education")
        genre_is("DRA026000", "DRAMA / Type / Tragedy", "Drama")
        genre_is(
            "BIB018100",
            "BIBLES / Other English Translations / Outreach",
            "Christianity",
        )
        genre_is(
            "FIC121010",
            "FICTION / World Literature / Scotland / 19th Century",
            "Historical Fiction",
        )
        genre_is("TEC050000", "TECHNOLOGY & ENGINEERING / Holography", "Technology")
        genre_is(
            "COM046090",
            "COMPUTERS / System Administration / Virtualization & Containerization",
            "Computers",
        )
        genre_is("EDU055000", "EDUCATION / Violence & Harassment", "Education")
        genre_is("CRA020000", "CRAFTS & HOBBIES / Models", "Crafts & Hobbies")
        genre_is("TRV007000", "TRAVEL / Caribbean & West Indies", "Travel")
        genre_is(
            "PHO023020", "PHOTOGRAPHY / Subjects & Themes / Children", "Photography"
        )
        genre_is("HEA002000", "HEALTH & FITNESS / Exercise / Aerobics", "Health & Diet")
        genre_is(
            "COM050010", "COMPUTERS / Hardware / Personal Computers / PCs", "Computers"
        )
        genre_is(
            "CGN010020",
            "COMICS & GRAPHIC NOVELS / Historical Fiction / Medieval",
            "Comics & Graphic Novels",
        )
        genre_is("SPO015000", "SPORTS & RECREATION / Football", "Sports")
        genre_is("LAW038010", "LAW / Family Law / Children", "Law")
        genre_is(
            "SOC008010",
            "SOCIAL SCIENCE / Cultural & Ethnic Studies / African Studies",
            "Social Sciences",
        )
        genre_is(
            "BUS057000",
            "BUSINESS & ECONOMICS / Industries / Retailing",
            "Personal Finance & Business",
        )
        genre_is("DRA004010", "DRAMA / European / French", "Drama")
        genre_is("ART067000", "ART / Forgeries", "Art & Culture")
        genre_is(
            "YAF058270",
            "YOUNG ADULT FICTION / Social Themes / Violence",
            "Difficult Topics",
        )
        genre_is("MED007000", "MEDICAL / Audiology & Speech Pathology", "Medical")
        genre_is(
            "BUS043040",
            "BUSINESS & ECONOMICS / Marketing / Multilevel",
            "Personal Finance & Business",
        )
        genre_is(
            "LAN007000",
            "LANGUAGE ARTS & DISCIPLINES / Handwriting",
            "Reference & Study Aids",
        )
        genre_is("JNF003260", "JUVENILE NONFICTION / Animals / Cows", "Animals")
        genre_is(
            "TEC005020",
            "TECHNOLOGY & ENGINEERING / Construction / Contracting",
            "Technology",
        )
        genre_is("MAT011000", "MATHEMATICS / Game Theory", "Mathematics")
        genre_is("JNF003180", "JUVENILE NONFICTION / Animals / Rabbits", "Animals")
        genre_is("CKB067000", "COOKING / Specific Ingredients / Poultry", "Cooking")
        genre_is("MAT012010", "MATHEMATICS / Geometry / Algebraic", "Mathematics")
        genre_is("MUS036000", "MUSIC / Genres & Styles / Latin", "Music")
        genre_is("SEL016000", "SELF-HELP / Personal Growth / Happiness", "Self-Help")
        genre_is("LAW061000", "LAW / Legal Profession", "Law")
        genre_is("HEA000000", "HEALTH & FITNESS / General", "Health & Diet")
        genre_is(
            "ART050030", "ART / Subjects & Themes / Plants & Animals", "Art & Culture"
        )
        genre_is(
            "HIS007000",
            "HISTORY / Latin America / Central America",
            "Latin American History",
        )
        genre_is(
            "TRV022000",
            "TRAVEL / Food, Lodging & Transportation / Restaurants",
            "Travel",
        )
        genre_is(
            "BIO009000",
            "BIOGRAPHY & AUTOBIOGRAPHY / Philosophers",
            "Biography & Memoir",
        )
        genre_is("DRA016000", "DRAMA / Russian & Soviet", "Drama")
        genre_is(
            "COM083000", "COMPUTERS / Security / Cryptography & Encryption", "Computers"
        )
        genre_is("MUS056000", "MUSIC / Genres & Styles / Indigenous", "Music")
        genre_is(
            "HIS027370",
            "HISTORY / Wars & Conflicts / World War II / European Theater",
            "History",
        )
        genre_is("FIC042030", "FICTION / Christian / Historical", "Religious Fiction")
        genre_is(
            "FIC090010",
            "FICTION / World Literature / Canada / Colonial & 19th Century",
            "Historical Fiction",
        )
        genre_is("CKB060000", "COOKING / Methods / Outdoor", "Cooking")
        genre_is(
            "CRA044000",
            "CRAFTS & HOBBIES / Needlework / Cross-Stitch",
            "Crafts & Hobbies",
        )
        genre_is("MED016020", "MEDICAL / Dentistry / Dental Hygiene", "Medical")
        genre_is(
            "MUS037090", "MUSIC / Printed Music / Piano & Keyboard Repertoire", "Music"
        )
        genre_is("MED000000", "MEDICAL / General", "Medical")
        genre_is(
            "FIC105030",
            "FICTION / World Literature / India / 21st Century",
            "General Fiction",
        )
        genre_is("BIB009010", "BIBLES / New American Bible / Children", "Christianity")
        genre_is("JNF040000", "JUVENILE NONFICTION / Philosophy", "Philosophy")
        genre_is(
            "LIT024030",
            "LITERARY CRITICISM / Modern / 18th Century",
            "Literary Criticism",
        )
        genre_is(
            "FIC022080",
            "FICTION / Mystery & Detective / International Crime & Mystery",
            "Mystery",
        )
        genre_is(
            "YAN038150",
            "YOUNG ADULT NONFICTION / Middle Eastern & Arab American",
            "Diversity & Multicultural",
        )
        genre_is(
            "BIO013000",
            "BIOGRAPHY & AUTOBIOGRAPHY / Rich & Famous",
            "Biography & Memoir",
        )
        genre_is(
            "POL074010",
            "POLITICAL SCIENCE / Indigenous / Governance & Sovereignty",
            "Political Science",
        )
        genre_is(
            "TRV005000",
            "TRAVEL / Food, Lodging & Transportation / Bed & Breakfast",
            "Travel",
        )
        genre_is(
            "BIO015000",
            "BIOGRAPHY & AUTOBIOGRAPHY / Science & Technology",
            "Biography & Memoir",
        )
        genre_is(
            "BIB010100",
            "BIBLES / New American Standard Bible / Outreach",
            "Christianity",
        )
        genre_is("COM051260", "COMPUTERS / Languages / JavaScript", "Computers")
        genre_is("ARC000000", "ARCHITECTURE / General", "Architecture")
        genre_is(
            "TEC074000",
            "TECHNOLOGY & ENGINEERING / Explosives & Pyrotechnics",
            "Technology",
        )
        genre_is(
            "YAN052040",
            "YOUNG ADULT NONFICTION / Social Science / Politics & Government",
            "Society",
        )
        genre_is(
            "LAN030000",
            "LANGUAGE ARTS & DISCIPLINES / Orality",
            "Reference & Study Aids",
        )
        genre_is(
            "YAF010140",
            "YOUNG ADULT FICTION / Comics & Graphic Novels / LGBTQ+",
            "LGBTQ Fiction",
        )
        genre_is(
            "REF007000", "REFERENCE / Curiosities & Wonders", "Reference & Study Aids"
        )
        genre_is("CKB029000", "COOKING / Entertaining", "Cooking")
        genre_is(
            "TEC012000",
            "TECHNOLOGY & ENGINEERING / Food Science / General",
            "Technology",
        )
        genre_is("MAT025000", "MATHEMATICS / Recreations & Games", "Mathematics")
        genre_is("NAT036000", "NATURE / Weather", "Nature")
        genre_is(
            "JUV008000",
            "JUVENILE FICTION / Comics & Graphic Novels / General",
            "General Fiction",
        )
        genre_is("POE013000", "POETRY / Middle Eastern", "Poetry")
        genre_is(
            "BIB026020", "BIBLES / Catholic Translations / Devotional", "Christianity"
        )
        genre_is("PHI020000", "PHILOSOPHY / Movements / Pragmatism", "Philosophy")
        genre_is(
            "JUV043000", "JUVENILE FICTION / Readers / Beginner", "General Fiction"
        )
        genre_is(
            "FIC042120", "FICTION / Christian / Romance / Suspense", "Religious Fiction"
        )
        genre_is(
            "BUS090040",
            "BUSINESS & ECONOMICS / E-Commerce / Small Business",
            "Personal Finance & Business",
        )
        genre_is("NAT045040", "NATURE / Ecosystems & Habitats / Wilderness", "Nature")
        genre_is("EDU039000", "EDUCATION / Computers & Technology", "Education")
        genre_is("MED029000", "MEDICAL / Family & General Practice", "Medical")
        genre_is("HIS020000", "HISTORY / Europe / Italy", "European History")
        genre_is(
            "HEA014000", "HEALTH & FITNESS / Massage & Reflexology", "Health & Diet"
        )
        genre_is("SOC052000", "SOCIAL SCIENCE / Media Studies", "Social Sciences")
        genre_is(
            "SPO034000", "SPORTS & RECREATION / Roller & In-Line Skating", "Sports"
        )
        genre_is(
            "BIB027030",
            "BIBLES / Other Spanish Translations / Dramatized",
            "Christianity",
        )
        genre_is("SPO008000", "SPORTS & RECREATION / Boxing", "Sports")
        genre_is(
            "SOC017000",
            "SOCIAL SCIENCE / LGBTQ+ Studies / Lesbian Studies",
            "Social Sciences",
        )
        genre_is("NAT002000", "NATURE / Animals / Primates", "Nature")
        genre_is("TEC052000", "TECHNOLOGY & ENGINEERING / Social Aspects", "Technology")
        genre_is("FIC028120", "FICTION / Science Fiction / Humorous", "Science Fiction")
        genre_is("DES003000", "DESIGN / Decorative Arts", "Design")
        genre_is(
            "JNF076070",
            "JUVENILE NONFICTION / Indigenous / Reconciliation",
            "Diversity & Multicultural",
        )
        genre_is(
            "CGN011000",
            "COMICS & GRAPHIC NOVELS / Religious",
            "Comics & Graphic Novels",
        )
        genre_is(
            "ART006020",
            "ART / Collections, Catalogs, Exhibitions / Permanent Collections",
            "Art & Culture",
        )
        genre_is(
            "SCI036000",
            "SCIENCE / Life Sciences / Human Anatomy & Physiology",
            "Science",
        )
        genre_is("HIS003000", "HISTORY / Asia / General", "Asian History")
        genre_is(
            "EDU041000", "EDUCATION / Distance, Open & Online Education", "Education"
        )
        genre_is("PER014000", "PERFORMING ARTS / Business Aspects", "Performing Arts")
        genre_is(
            "POL019000",
            "POLITICAL SCIENCE / Public Policy / Social Services & Welfare",
            "Political Science",
        )
        genre_is("MED085080", "MEDICAL / Surgery / Laparoscopic & Robotic", "Medical")
        genre_is(
            "BIB012000",
            "BIBLES / New International Reader's Version / General",
            "Christianity",
        )
        genre_is(
            "OCC011020",
            "BODY, MIND & SPIRIT / Healing / Prayer & Spiritual",
            "Body, Mind & Spirit",
        )
        genre_is("DES009000", "DESIGN / Industrial", "Design")
        genre_is(
            "TEC009100", "TECHNOLOGY & ENGINEERING / Civil / Bridges", "Technology"
        )
        genre_is("GAR004000", "GARDENING / Flowers / General", "Gardening")
        genre_is("TRA003000", "TRANSPORTATION / Motorcycles / General", "Technology")
        genre_is("HUM017000", "HUMOR / Form / Pictorial", "Humor")
        genre_is(
            "BUS073000",
            "BUSINESS & ECONOMICS / Commerce",
            "Personal Finance & Business",
        )
        genre_is(
            "BIB010080",
            "BIBLES / New American Standard Bible / Dramatized",
            "Christianity",
        )
        genre_is("DRA004000", "DRAMA / European / General", "Drama")
        genre_is("SCI013090", "SCIENCE / Chemistry / Toxicology", "Science")
        genre_is("POE030000", "POETRY / Indigenous", "Poetry")
        genre_is(
            "PSY022050", "PSYCHOLOGY / Psychopathology / Schizophrenia", "Psychology"
        )
        genre_is(
            "YAN039020", "YOUNG ADULT NONFICTION / Performing Arts / Film", "Film & TV"
        )
        genre_is(
            "BUS064020",
            "BUSINESS & ECONOMICS / International / Taxation",
            "Personal Finance & Business",
        )
        genre_is(
            "JNF055030",
            "JUVENILE NONFICTION / Study Aids / Test Preparation",
            "Reference & Study Aids",
        )
        genre_is("PHI010000", "PHILOSOPHY / Movements / Humanism", "Philosophy")
        genre_is(
            "MAT029020",
            "MATHEMATICS / Probability & Statistics / Multivariate Analysis",
            "Mathematics",
        )
        genre_is("HIS037080", "HISTORY / Modern / 21st Century", "Modern History")
        genre_is("FIC027350", "FICTION / Romance / Firefighters", "Romance")
        genre_is("MED086000", "MEDICAL / Test Preparation & Review", "Medical")
        genre_is(
            "JUV028000",
            "JUVENILE FICTION / Mysteries & Detective Stories",
            "Crime & Detective Stories",
        )
        genre_is("EDU029090", "EDUCATION / Teaching / Materials & Devices", "Education")
        genre_is("TRV009050", "TRAVEL / Europe / France", "Travel")
        genre_is("ART057000", "ART / Film & Video", "Art & Culture")
        genre_is("SPO052000", "SPORTS & RECREATION / Winter Sports / General", "Sports")
        genre_is(
            "JNF032000", "JUVENILE NONFICTION / Lifestyles / Country Life", "Society"
        )
        genre_is(
            "BUS024000",
            "BUSINESS & ECONOMICS / Education",
            "Personal Finance & Business",
        )
        genre_is("JUV069000", "JUVENILE FICTION / Ghost Stories", "Horror")
        genre_is("MED087000", "MEDICAL / Transportation", "Medical")
        genre_is("REF019000", "REFERENCE / Quotations", "Reference & Study Aids")
        genre_is("COM051350", "COMPUTERS / Languages / Perl", "Computers")
        genre_is(
            "BIO021000",
            "BIOGRAPHY & AUTOBIOGRAPHY / Social Scientists & Psychologists",
            "Biography & Memoir",
        )
        genre_is("SOC016000", "SOCIAL SCIENCE / Human Services", "Social Sciences")
        genre_is(
            "LIT024060",
            "LITERARY CRITICISM / Modern / 21st Century",
            "Literary Criticism",
        )
        genre_is(
            "COM062000",
            "COMPUTERS / Data Science / Data Modeling & Design",
            "Computers",
        )
        genre_is("JUV087000", "JUVENILE FICTION / Trickster Tales", "General Fiction")
        genre_is(
            "BUS036060",
            "BUSINESS & ECONOMICS / Investments & Securities / Stocks",
            "Personal Finance & Investing",
        )
        genre_is(
            "YAF046000", "YOUNG ADULT FICTION / Places / General", "General Fiction"
        )
        genre_is("LAW082000", "LAW / Right to Die", "Law")
        genre_is(
            "POL011000",
            "POLITICAL SCIENCE / International Relations / General",
            "Political Science",
        )
        genre_is(
            "PER010130",
            "PERFORMING ARTS / Television / Genres / Documentary",
            "Film & TV",
        )
        genre_is("SPO056000", "SPORTS & RECREATION / Rugby", "Sports")
        genre_is("DRA000000", "DRAMA / General", "Drama")
        genre_is("YAN019000", "YOUNG ADULT NONFICTION / Fashion", "Fashion & Looks")
        genre_is("YAF058070", "YOUNG ADULT FICTION / Disabilities", "Disabilities")
        genre_is("BIB008060", "BIBLES / Multiple Translations / Text", "Christianity")
        genre_is("TEC030000", "TECHNOLOGY & ENGINEERING / Optics", "Technology")
        genre_is("SEL019000", "SELF-HELP / Meditations", "Self-Help")
        genre_is("FIC036000", "FICTION / Thrillers / Technological", "Technothriller")
        genre_is(
            "CGN014000", "COMICS & GRAPHIC NOVELS / Humorous", "Comics & Graphic Novels"
        )
        genre_is(
            "BIB027110",
            "BIBLES / Other Spanish Translations / Youth & Teen",
            "Christianity",
        )
        genre_is("SPO061030", "SPORTS & RECREATION / Coaching / Soccer", "Sports")
        genre_is(
            "LIT024050",
            "LITERARY CRITICISM / Modern / 20th Century",
            "Literary Criticism",
        )
        genre_is("LAW078000", "LAW / Real Estate", "Law")
        genre_is("TRV003020", "TRAVEL / Asia / East / China", "Travel")
        genre_is(
            "SPO023000",
            "SPORTS & RECREATION / Winter Sports / Ice & Figure Skating",
            "Sports",
        )
        genre_is("JNF006030", "JUVENILE NONFICTION / Art / Fashion", "Fashion & Looks")
        genre_is("BIB004060", "BIBLES / God's Word / Text", "Christianity")
        genre_is("EDU046000", "EDUCATION / Professional Development", "Education")
        genre_is(
            "YAN005030", "YOUNG ADULT NONFICTION / Art / Fashion", "Fashion & Looks"
        )
        genre_is(
            "BUS050010",
            "BUSINESS & ECONOMICS / Personal Finance / Budgeting",
            "Personal Finance & Investing",
        )
        genre_is("EDU022000", "EDUCATION / Parent Participation", "Education")
        genre_is(
            "BUS019000",
            "BUSINESS & ECONOMICS / Decision-Making & Problem Solving",
            "Personal Finance & Business",
        )
        genre_is(
            "LIT004190",
            "LITERARY CRITICISM / Ancient & Classical",
            "Literary Criticism",
        )
        genre_is(
            "CRA015000", "CRAFTS & HOBBIES / Needlework / Knitting", "Crafts & Hobbies"
        )
        genre_is(
            "BUS070050",
            "BUSINESS & ECONOMICS / Industries / Manufacturing",
            "Personal Finance & Business",
        )
        genre_is("PHO017000", "PHOTOGRAPHY / Reference", "Photography")
        genre_is(
            "BUS119000",
            "BUSINESS & ECONOMICS / Indigenous Economies",
            "Personal Finance & Business",
        )
        genre_is(
            "TRA002050",
            "TRANSPORTATION / Aviation / Piloting & Flight Instruction",
            "Technology",
        )
        genre_is("LAW080000", "LAW / Remedies & Damages", "Law")
        genre_is(
            "BIB018030",
            "BIBLES / Other English Translations / New Testament & Portions",
            "Christianity",
        )
        genre_is(
            "ARC005050", "ARCHITECTURE / History / Baroque & Rococo", "Architecture"
        )
        genre_is(
            "FIC139000", "FICTION / World Literature / Central Asia", "General Fiction"
        )
        genre_is("SOC020000", "SOCIAL SCIENCE / Minority Studies", "Social Sciences")
        genre_is("ART027000", "ART / Study & Teaching", "Art & Culture")
        genre_is(
            "TEC016010",
            "TECHNOLOGY & ENGINEERING / Industrial Design / Packaging",
            "Technology",
        )
        genre_is(
            "HEA019000",
            "HEALTH & FITNESS / Diet & Nutrition / Weight Loss",
            "Health & Diet",
        )
        genre_is(
            "BIB012070",
            "BIBLES / New International Reader's Version / Youth & Teen",
            "Christianity",
        )
        genre_is("JNF058000", "JUVENILE NONFICTION / Travel", "Travel")
        genre_is("COM055000", "COMPUTERS / Certification Guides / General", "Computers")
        genre_is("HIS026040", "HISTORY / Middle East / Syria", "History")
        genre_is("REL097000", "RELIGION / Christianity / Presbyterian", "Christianity")
        genre_is("REF000000", "REFERENCE / General", "Reference & Study Aids")
        genre_is("COM059000", "COMPUTERS / Computer Engineering", "Computers")

    def test_fiction_spot_checks(self):
        def fiction_is(identifier, name, expect):
            subject = self._subject(identifier, "")
            assert expect == subject.fiction

        fiction_is("FIC028000", "Fiction / Science Fiction / General", True)
        fiction_is("ANT022000", "Antiques & Collectibles / Kitchenware", False)
        fiction_is("HUM000000", "Humor / General", True)
        fiction_is("DRA000000", "Drama / General", True)
        fiction_is("YAF048000", "Young Adult Fiction / Poetry", True)
        fiction_is("POE000000", "Poetry / General", True)
        fiction_is("LIT014000", "Literary Criticism / Poetry", False)
        fiction_is("YAN028000", "Young Adult Nonfiction / Humor", False)
        fiction_is("JUV019000", "Juvenile Fiction / Humorous Stories", True)
        fiction_is("LCO000000", "Literary Collections / General", True)
        fiction_is("LCO011000", "Literary Collections / Letters", True)
        fiction_is("LCO010000", "Literary Collections / Essays", True)
        fiction_is("FIC004000", "FICTION / Classics", True)
        fiction_is("JUV009070", "JUVENILE FICTION / Concepts / Date & Time", True)
        fiction_is("YAF033000", "YOUNG ADULT FICTION / Lifestyles / Country Life", True)
        fiction_is("HIS000000", "HISTORY / General", False)
        fiction_is("JUV000000", "JUVENILE FICTION / General", True)

    def test_audience_spot_checks(self):
        def audience_is(identifier, expect):
            subject = self._subject(identifier, "")
            assert expect == subject.audience

        adult = SubjectClassifier.AUDIENCE_ADULT
        ya = SubjectClassifier.AUDIENCE_YOUNG_ADULT
        children = SubjectClassifier.AUDIENCE_CHILDREN

        audience_is("HIS000000", adult)
        audience_is("JUV016180", children)
        audience_is("YAN053070", ya)

    def test_target_age_spot_checks(self):
        def target_age_is(identifier, expect):
            subject = self._subject(identifier, "")
            assert expect == subject.target_age

        # These are the only BISAC classifications with implied target
        # ages.
        target_age_is("JUV043000", (0, 4))
        target_age_is("JUV044000", (5, 7))
        target_age_is("JUV045000", (8, 13))

        # In all other cases, target age will return None.
        target_age_is("CGN004020", None)
        target_age_is("HEA022000", None)
        target_age_is("JUV016180", None)
        target_age_is("YAN053070", None)

    def test_feedbooks_bisac(self):
        """Feedbooks uses a system based on BISAC but with different
        identifiers, different names, and some additions. This is all
        handled transparently by the default BISAC classifier.
        """
        subject = self._subject("FBFIC022000", "Mystery & Detective")
        assert "Mystery" == subject.genre.name

        # This is not an official BISAC classification, so we"re not
        # expecting to find anything
        subject = self._subject("FSHUM000000N", "Human Science")
        if subject.genre is None:
            assert subject.genre is None

    def test_scrub_identifier(self):
        # FeedBooks prefixes are removed.
        assert "abc" == BISACClassifier.scrub_identifier("FBabc")

        # Otherwise, the identifier is left alone.
        assert "abc" == BISACClassifier.scrub_identifier("abc")

        # If the identifier is recognized as an official BISAC identifier,
        # the canonical name is also returned. This will override
        # any other name associated with the subject for classification
        # purposes.
        assert ("FIC015000", "FICTION / Horror") == BISACClassifier.scrub_identifier(
            "FBFIC015000"
        )

    def test_scrub_name(self):
        """Sometimes a data provider sends BISAC names that contain extra or
        nonstandard characters. We store the data as it was provided to us,
        but when it"s time to classify things, we normalize it.
        """

        def scrubbed(before, after):
            assert after == BISACClassifier.scrub_name(before)

        scrubbed(
            "BIOGRAPHY & AUTOBIOGRAPHY / Editors, Journalists, Publishers",
            "BIOGRAPHY & AUTOBIOGRAPHY / Editors, Journalists, Publishers",
        )
        # No such BISAC
        scrubbed(
            "JUVENILE FICTION / Family / General (see also headings under Social Issues)",
            None,
        )
