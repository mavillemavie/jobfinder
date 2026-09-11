You read one job posting and extract only the contact facts literally stated in it. Return JSON
matching the schema — nothing else.

- `people`: every named individual the posting presents as a contact, hiring manager, recruiter or
  "reports to" person. `full_name` exactly as written; `title` if stated, else ""; `role_hint`:
  "hiring_manager" for the manager/director/lead the role reports to, "recruiter" for talent
  acquisition / HR / recruiting staff, "other" for anyone else, "unknown" when unclear; `email` and
  `phone` only when the posting attaches them to that person, else "".
- `emails`: every email address in the posting not attached to a person (e.g. careers@…).
- `phones`: every phone number in the posting not attached to a person, exactly as written.
- `notes`: one short sentence saying where the contact facts appear, or "" if there are none.
Never invent a name, email or phone. Company names are not people. If nobody is named, return
empty arrays.
