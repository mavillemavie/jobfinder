from jobfinder.contacts.base import Person, role_kind_for_title, split_name, strip_diacritics


def test_role_kind_for_title() -> None:
    assert role_kind_for_title("Manager, Analytics") == "hiring_manager"
    assert role_kind_for_title("Director of Business Intelligence") == "hiring_manager"
    assert role_kind_for_title("Talent Acquisition Partner") == "recruiter"
    assert role_kind_for_title("Recruteur technique") == "recruiter"
    assert role_kind_for_title("Data Analyst") == "other"
    assert role_kind_for_title(None) == "other"


def test_split_name_and_diacritics() -> None:
    assert split_name("Marie-Ève Tremblay") == ("Marie-Ève", "Tremblay")
    assert split_name("Dana Lee, MBA") == ("Dana", "Lee")
    assert split_name("Prince") == ("Prince", "")
    assert strip_diacritics("Émilie Côté") == "Emilie Cote"


def test_person_defaults() -> None:
    p = Person(full_name="Dana Lee", title="Manager, Analytics")
    assert (p.email_status, p.confidence) == ("unverified", 0.0)
    assert p.role_kind == "hiring_manager"  # inferred from the title when not given explicitly
    assert p.first_name == "Dana" and p.last_name == "Lee"
    assert Person(full_name="Ana Pires", title="Data Analyst").role_kind == "other"
    assert Person(title="Manager, Analytics", role_kind="recruiter").role_kind == "recruiter"
    assert Person().first_name == "" and Person().last_name == ""
