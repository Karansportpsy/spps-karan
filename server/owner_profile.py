"""Verified SPPS owner: public founder presentation metadata, not an auth grant.

Only the protected backend and confirmed Supabase AAL2 owner-claim may grant
is_admin. A matching email in a public signup does not confer privileges.
"""
OWNER_EMAIL = "karansportpsy@gmail.com"
OWNER_NAME = "Dr. Karanbir Singh"
OWNER_ROLE = "Senior Lead in Sports and Performance Psychology"
OWNER_EXPERIENCE_YEARS = 12
OWNER_PHOTO_URL = "https://www.winmindperform.com/assets/images/dr-karanbir-singh-portrait.jpg"
OWNER_QUALIFICATIONS = "PhD (Sports Science) · MA (Sport Psychology)"
OWNER_SPECIALISMS = "Mental conditioning · Cognitive performance · Sport injury psychology"
OWNER_ACHIEVEMENTS = (
    "Senior Lead in Sports and Performance Psychology. "
    "12+ years of professional experience delivering mental conditioning "
    "and cognitive performance frameworks for athletes."
)

def is_featured_owner(person, profile) -> bool:
    return bool(
        person.is_admin
        and person.email.strip().lower() == OWNER_EMAIL
        and person.active
        and profile.status == "approved"
        and profile.email_verified_at is not None
        and profile.supabase_auth_uid
        and profile.credentials_verified
        and profile.public_approved
    )
