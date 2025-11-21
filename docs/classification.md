# Classification in E-kirjasto

## Subject

An ODL2 feed contains subjects that attempt to provide classification information about a book. The relevant subjects to
us are

- `BISAC` that describes genre and fiction/nonfiction
- `schema:audience` that describes the target audience
- `schema:Audience` that describes the target audience and target age range
- `schema:typicalAgeRange`that describes the target age range

```sh
"subject": [
    {
        "code": "FIC000000",
        "name": "FICTION / General",
        "scheme": "http://www.bisg.org/standards/bisac_subject/"
    },
    {
        "code": "Adult",
        "name": "Adult",
        "scheme": "http://schema.org/audience"
    },
    {
        "code": "READ0000",
        "scheme": "http://schema.org/Audience",
        "name": "Adult",
        "links": [
            {
            "type": "application/opds+json",
            "href": "https://market.cantook.com/top/top.json?audiences=READ0000&languages=en"
            }
        ]
    },
    {
        "code": "12-17",
        "name": "12-17",
        "scheme": "http://schema.org/typicalAgeRange"
    }
]
```

## SubjectClassifier

When a feed is imported into the circulation database, each subject is set a `subject type` based on the subject scheme
url. The urls are mapped to specific `SubjectClassifier`s and are `ClassifierConstants`.

| subject scheme                                 | subject type                          | classifier                  |
|------------------------------------------------|---------------------------------------|-----------------------------|
| "http://www.bisg.org/standards/bisac_subject/" | BISAC                                 | BISACClassifier             |
| "http://schema.org/audience"                   | schema:audience                       | SchemaAudienceClassifier    |
| "http://schema.org/Audience"                   | De Marque                             | DeMarqueClassifier          |
| "http://schema.org/typicalAgeRange"            | schema:typicalAgeRange                | AgeRangeClassifier          |
|  ?                                             | tag                                   | KeywordClassifier           |
| "http://librarysimplified.org/terms/           | "http://librarysimplified.org/terms/  | SimplifiedGenreClassifier   |
|  genres/Simplified/"                           |  genres/Simplified/"                  |                             |
| "http://librarysimplified.org/terms/           | "http://librarysimplified.org/terms/  |                             |
|  fiction/"                                     |  fiction/"                            | SimplifiedFictionClassifier |

The different classifiers will set the subject with approriate fiction status, genre, audience and target age.

The simplified schemes do not appear in any of the feeds. When a library staff member manually edits classifications in
the admin UI, those classification subjects get such schemes and type.

## Genre

As mentoined, a `SubjectClassifier` maps the subject with a genre. At the moment, only `BISAC` subjects (and simplified
genre) map genres. The `BISACClassifier` maps the subject name to a genre based on regular expression rules.

There are over 100 genres divided to fiction genres and nonfictions genres. One genre can be associated to several
subjects.

## WorkClassifier

When calculating a work's presentation information (`calculate_presentation()`), the associated subjects are assessed
in `prepare_classification()`.
The first step is to `extract_subject_data()` which calls the appropriate `SubjectClassifier` to set the subject's
fiction status, genre, audience and target age.
The `prepare_classification()` function then collects counts of the subject's properties if they are relevant - this
depends on e.g. the subject type. If the classifications have been manually edited in the admin UI, any other subjects
are ignored.
Finally, based on the counts, classification details of a work are set in `classify_work()`: fiction/nonfiction,
genres, audience and target age.
