"""Fictional fixtures for the automated tests and the local preview. Not application code: the app cannot
seed data, and the production image copies only server/, migrations/ and web/."""
from pathlib import Path

AVATAR_DIR=Path(__file__).resolve().parent/"avatars"

def _portrait(key):
    file=AVATAR_DIR/f"{key}.webp"
    return file.read_bytes() if file.is_file() else None

from datetime import date, timedelta
from server.models import now
from sqlalchemy import select
from server.models import Tenant,Practitioner,Service,Athlete,Case,Factor,Observation,Intervention,PractitionerProfile,AppointmentSlot
from server.security import make_password

SERVICES=[
 ('performance-consultation','Performance Psychology Consultation','Individual support for confidence, attention, pressure and competition routines.','45–60 minute session','Price shared on enquiry'),
 ('injury-return','Injury Psychology & Return to Sport','Psychological support during rehabilitation, confidence rebuilding and return-to-sport preparation.','Initial assessment + follow-ups','Price shared on enquiry'),
 ('parent-guidance','Parent / Guardian Consultation','Practical guidance on communication, sport pressure and supporting young athletes.','45 minute consultation','Price shared on enquiry'),
 ('mental-skills-programme','Competition Mental Skills Programme','Structured, practitioner-guided training in imagery, breathing, refocusing and performance routines.','Multi-session programme','Tailored quotation')]
INTERVENTIONS=[
 ('Reset cue and refocusing','Attention refocusing after a mistake','Agree a short cue, practise a one-breath reset during training, reflect on response.','Practitioner-selected technique; contextual support rather than a fixed therapeutic protocol.','https://appliedsportpsych.org/'),
 ('Imagery rehearsal','Competition planning and confidence','Agree a task-specific script, practise short guided imagery and review its usefulness.','Use with individual adaptation; imagery ability and response vary.','https://appliedsportpsych.org/'),
 ('Paced breathing','Arousal regulation and perceived readiness','Practise comfortable, gentle paced breathing; stop if lightheaded or distressed.','Not a substitute for medical care; avoid prescriptive breathing rates for every athlete.','https://www.nhs.uk/mental-health/self-help/guides-tools-and-activities/breathing-exercises-for-stress/')]

def seed(db):
    tenant=db.scalar(select(Tenant).where(Tenant.slug=='model-c-demo'))
    if tenant:return tenant
    tenant=Tenant(slug='model-c-demo',display_name='Sport Psychology Practitioner Suite')
    db.add(tenant);db.flush()
    user=Practitioner(tenant_id=tenant.id,email='practitioner@modelc.test',name='Demo Practitioner',password_hash=make_password('DemoOnly!2026'),tier='core',is_admin=True)
    db.add(user);db.flush()
    db.add(PractitionerProfile(practitioner_id=user.id,qualifications='PhD Sport Psychology (demonstration credential — not verified)',experience_years=8,achievements='Synthetic demonstration achievements; replace following credential verification',specialisms='Performance, competition pressure, injury psychology',public_approved=True))
    from datetime import datetime, timezone
    tomorrow=datetime.now(timezone.utc)+timedelta(days=2)
    db.add(AppointmentSlot(practitioner_id=user.id,starts_at=tomorrow.replace(hour=11,minute=0,second=0,microsecond=0),duration_minutes=50,amount_paise=150000))
    db.add(AppointmentSlot(practitioner_id=user.id,starts_at=(tomorrow+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0),duration_minutes=50,amount_paise=150000))
    for slug,title,description,duration,price in SERVICES:
        db.add(Service(tenant_id=tenant.id,slug=slug,title=title,description=description,duration=duration,price_label=price))
    athletes=[('MC-001','Aarav Mehta','Badminton'),('MC-002','Maya Rao','Swimming'),('MC-003','Dev Kapoor','Shooting')]
    athlete_models=[]
    for code,name,sport in athletes:
        a=Athlete(tenant_id=tenant.id,owner_practitioner_id=user.id,code=code,display_name=name,sport=sport,athlete_group='Youth',service_consent_recorded_at=now())
        db.add(a);db.flush();athlete_models.append(a)
    cases=[]
    for a,title,category,goal in zip(athlete_models,['Competition routine','Race-day focus','Competition confidence'],['Performance psychology']*3,['Use agreed reset cue during training','Establish a consistent pre-race focus routine','Practise competition preparation plan']):
        c=Case(tenant_id=tenant.id,athlete_id=a.id,owner_practitioner_id=user.id,title=title,category=category,goal=goal,next_review=(date.today()+timedelta(days=7)).isoformat())
        db.add(c);db.flush();cases.append(c)
    facts=[('Confidence', 'Daily monitoring','monitoring',0,10,'higher-supportive'),('Stress','Daily monitoring','monitoring',0,10,'lower-supportive'),('Motivation','Daily monitoring','monitoring',0,10,'higher-supportive'),('Competitive anxiety','Daily monitoring','monitoring',0,10,'contextual')]
    fs=[]
    for label,group,kind,lo,hi,direction in facts:
        f=Factor(tenant_id=tenant.id,label=label,group_name=group,kind=kind,unit='points',low=lo,high=hi,higher_is=direction);db.add(f);db.flush();fs.append(f)
    for idx,vals in enumerate([[5,7,6,5],[6,6,6,5],[6,5,7,4]]):
        for f,v in zip(fs,vals):
            db.add(Observation(tenant_id=tenant.id,athlete_id=athlete_models[0].id,case_id=cases[0].id,factor_id=f.id,value=v,observed_on=(date.today()-timedelta(days=14-idx*7)).isoformat(),source='synthetic-demo'))
    for name,purpose,method,note,url in INTERVENTIONS:
        db.add(Intervention(tenant_id=tenant.id,name=name,purpose=purpose,method=method,evidence_note=note,reference_url=url))
    db.commit();return tenant

# Additional, entirely fictional and idempotent validation fixtures. Never run in production.
DEMO_PRACTITIONERS=[
    # email, name, tier, photo, qualification, experience, specialisms, achievements
    ('practitioner@modelc.test','Dr. Ananya Rao','core','portrait_01','PhD (Sport Psychology) · MSc (Applied Psychology)',12,'Performance psychology · Anxiety management','Synthetic: Delivered mental skills programmes for national-level athletes; contributed to multidisciplinary performance workshops.'),
    ('rohan@modelc.test','Mr. Rohan Mehta','core','portrait_02','MSc (Sport & Exercise Psychology) · PG Diploma (Sport Psychology)',7,'Attention training · Football · Cricket','Synthetic: Supported collegiate and academy athletes; facilitated competition-readiness clinics.'),
    ('kavya@modelc.test','Ms. Kavya Nair','core','portrait_03','MA (Psychology) · Sport Psychology Certificate',6,'Youth sport · Motivation · Confidence','Synthetic: Designed parent-guidance seminars and youth athlete confidence workshops.'),
    ('arjun@modelc.test','Dr. Arjun Desai','pro','portrait_04','PhD (Applied Sport Psychology) · MSc (Psychology)',11,'Injury psychology · Return-to-sport confidence','Synthetic: Participated in fictional injury rehabilitation case conferences and professional education events.'),
    ('isha@modelc.test','Ms. Isha Sharma','pro','portrait_05','MSc (Sport Psychology) · Mental Skills Coaching Certificate',9,'Mental skills · Swimming · Athletics','Synthetic: Led fictional imagery and self-regulation training workshops across youth sport programmes.'),
    ('vikram@modelc.test','Mr. Vikram Sinha','core','portrait_06','MA (Psychology) · Certificate (Sport Psychology)',4,'Well-being · Grassroots development','Synthetic: Assisted fictional school sport education initiatives; eligible for public booking after verification.'),
]


def seed_extended_demo(db):
    """Enhance local fixtures only; safe to repeat and safe after upgrading a v0.1 demo DB.

    This method is only invoked with seed_demo=True. Data are fictitious and
    should never be copied to a live deployment or represented as real credentials.
    """
    from server.models import (PractitionerProfile, AppointmentSlot, AssessmentAdministration,
                         AssessmentSubscore, InjuryEpisode, InjuryCheckIn, Note, Assignment)
    from datetime import datetime, timezone, date, timedelta
    from sqlalchemy import select
    from server.security import make_password

    tenant=db.scalar(select(Tenant).where(Tenant.slug=='model-c-demo'))
    if not tenant:
        return
    practitioners={}
    for email,name,tier,photo,qualification,experience,specialisms,achievements in DEMO_PRACTITIONERS:
        practitioner=db.scalar(select(Practitioner).where(Practitioner.email==email))
        if not practitioner:
            practitioner=Practitioner(tenant_id=tenant.id,email=email,name=name,tier=tier,
                                     password_hash=make_password('DemoOnly!2026'),active=True)
            db.add(practitioner);db.flush()
        # Preserve Pro entitlements; upgrade legacy Plus records to the new Core tier.
        if practitioner.tier=='plus':practitioner.tier='core'
        # Do not overwrite valid Free/Pro tier choices on subsequent startup.
        if email=='practitioner@modelc.test' and practitioner.name=='Demo Practitioner':
            practitioner.name=name
        profile=db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id==practitioner.id))
        if not profile:
            profile=PractitionerProfile(practitioner_id=practitioner.id,qualifications=qualification,
                                        experience_years=experience,achievements=achievements,
                                        specialisms=specialisms,avatar_key=photo,avatar_bytes=_portrait(photo),public_approved=True,
                                        credentials_verified=True)  # fictional demo fixture only
            db.add(profile)
        elif email=='practitioner@modelc.test' and profile.qualifications.startswith(('PhD Sport Psychology (demonstration','Synthetic sport psychology qualification')):
            # Upgrade the starter demo profile, not a practitioner-edited credential.
            profile.qualifications=qualification
            profile.achievements=achievements
            profile.specialisms=specialisms
            profile.experience_years=experience
            profile.avatar_key=photo
            profile.avatar_bytes=_portrait(photo)
        if email.endswith('@modelc.test') and not profile.credentials_verified:
            profile.credentials_verified=True  # fictional demo fixture: pre-reviewed for local directory tests
        # Only the explicitly seeded fictional @modelc.test profiles are
        # considered pre-reviewed. Real self-registrations remain pending.
        if email.endswith('@modelc.test') and profile.status!='approved':
            profile.status='approved'
            profile.email_verified_at=now()
            profile.approved_at=now()
        if email=='vikram@modelc.test' and profile.public_approved is False:
            # Synthetic demo-only credentials have already been reviewed for directory testing.
            profile.public_approved=True
        practitioners[email]=practitioner
    db.flush()

    # Every verified profile can offer appointments on Free or Pro.
    for idx,(email,*_) in enumerate(DEMO_PRACTITIONERS):
        practitioner=practitioners[email]
        slots=db.scalars(select(AppointmentSlot).where(AppointmentSlot.practitioner_id==practitioner.id)).all()
        if slots:continue
        for day,hour in [(idx+2,10),(idx+4,15)]:
            starts=(datetime.now(timezone.utc)+timedelta(days=day)).replace(hour=hour,minute=0,second=0,microsecond=0)
            db.add(AppointmentSlot(practitioner_id=practitioner.id,starts_at=starts,
                                   duration_minutes=50,amount_paise=(130000+idx*15000)))
    db.flush()

    athlete_specs=[
        ('MC-001','Aarav Mehta','Badminton','practitioner@modelc.test','Competition routine','Competition confidence','Use a task-relevant reset cue after errors'),
        ('MC-002','Maya Rao','Swimming','practitioner@modelc.test','Injury rehabilitation confidence','Injury psychology','Develop confidence in a graded return-to-pool programme'),
        ('MC-003','Dev Kapoor','Shooting','practitioner@modelc.test','Shot routine consistency','Performance psychology','Develop a repeatable pre-shot focus routine'),
        ('MC-004','Ishan Verma','Football','practitioner@modelc.test','Return-to-training confidence','Injury psychology','Practise coping skills during structured rehabilitation'),
        ('MC-005','Tara Sen','Gymnastics','practitioner@modelc.test','Competition composure','Performance psychology','Use competition-specific imagery and self-talk'),
        ('MC-006','Kavya Menon','Cycling','practitioner@modelc.test','Pre-event readiness','Performance psychology','Identify controllable focus cues before time trials'),
        ('MC-007','Veer Malhotra','Athletics','arjun@modelc.test','Confidence during rehabilitation','Injury psychology','Build confidence for graded running exposure'),
        ('MC-008','Sia Bhat','Swimming','kavya@modelc.test','Focus and motivation','Performance psychology','Practise short task-oriented concentration routines'),
        ('MC-009','Kabir Gill','Football','vikram@modelc.test','Motivation and self-talk','Performance psychology','Strengthen practice engagement'),
    ]
    athletes=[]
    for code,name,sport,owner_email,title,category,goal in athlete_specs:
        athlete=db.scalar(select(Athlete).where(Athlete.tenant_id==tenant.id,Athlete.code==code))
        if not athlete:
            athlete=Athlete(tenant_id=tenant.id,owner_practitioner_id=practitioners[owner_email].id,
                            code=code,display_name=name,sport=sport,athlete_group='Youth (fictional)',service_consent_recorded_at=now())
            db.add(athlete);db.flush()
        case=db.scalar(select(Case).where(Case.athlete_id==athlete.id))
        if not case:
            case=Case(tenant_id=tenant.id,athlete_id=athlete.id,owner_practitioner_id=athlete.owner_practitioner_id,
                      title=title,category=category,goal=goal,next_review=(date.today()+timedelta(days=8)).isoformat())
            db.add(case);db.flush()
        athletes.append((athlete,case))

    definitions=[('Confidence','higher-supportive'),('Stress','lower-supportive'),
                 ('Motivation','higher-supportive'),('Competitive anxiety','contextual'),
                 ('Sleep quality','higher-supportive'),('Training load','contextual'),
                 ('Fatigue','lower-supportive'),('Recovery','higher-supportive'),
                 ('Energy','higher-supportive'),('Focus','higher-supportive')]
    factors={}
    for label,direction in definitions:
        factor=db.scalar(select(Factor).where(Factor.tenant_id==tenant.id,Factor.label==label,Factor.kind=='monitoring'))
        if not factor:
            factor=Factor(tenant_id=tenant.id,label=label,group_name='Daily check-in',kind='monitoring',
                          unit='0–10 self-report',low=0,high=10,higher_is=direction)
            db.add(factor);db.flush()
        factors[label]=factor

    for athlete_index,(athlete,case) in enumerate(athletes):
        # 21 days of descriptive observations with stable deterministic variation.
        existing={(o.factor_id,o.observed_on) for o in db.scalars(select(Observation).where(
            Observation.athlete_id==athlete.id,Observation.source=='synthetic-daily-checkin'))}
        for offset in range(20,-1,-1):
            obs_day=(date.today()-timedelta(days=offset)).isoformat()
            for factor_index,(label,_direction) in enumerate(definitions):
                factor=factors[label]
                if (factor.id,obs_day) in existing:continue
                baseline=5 + ((athlete_index*3+factor_index*2+offset)%5-2)
                # Load and stress fluctuate independently from recovery and sleep.
                v=max(1,min(9,baseline+(1 if label in ('Training load','Fatigue') and offset%3==0 else 0)))
                db.add(Observation(tenant_id=tenant.id,athlete_id=athlete.id,case_id=case.id,factor_id=factor.id,
                                   value=v,observed_on=obs_day,source='synthetic-daily-checkin',
                                   context='Fictional, practitioner-entered daily check-in; not clinical scores'))

        for offset,version in [(22,'synthetic-v1'),(8,'synthetic-v1')]:
            scored_on=(date.today()-timedelta(days=offset)).isoformat()
            instrument='Offline Performance Profile (fictional, not validated)'
            existing_run=db.scalar(select(AssessmentAdministration).where(
                AssessmentAdministration.athlete_id==athlete.id,
                AssessmentAdministration.instrument_name==instrument,
                AssessmentAdministration.observed_on==scored_on))
            if existing_run:continue
            run=AssessmentAdministration(tenant_id=tenant.id,athlete_id=athlete.id,case_id=case.id,
                                          instrument_name=instrument,instrument_version=version,
                                          observed_on=scored_on,note='Synthetic offline results; NOT a published test or normative score.')
            db.add(run);db.flush()
            rows=[('Confidence','Competition belief','Raw Score',5+athlete_index%5),
                  ('Confidence','Task-specific belief','Percentile',55+athlete_index*4+(1 if offset==8 else 6)),
                  ('Anxiety','Cognitive worries','Z-Score',round(-.5+athlete_index*.12,2)),
                  ('Motivation','Practice engagement','T-Score',46+athlete_index*2),
                  ('Attention','Focus under distraction','Sten',min(10,4+athlete_index%5))]
            for parent,sub,unit,value in rows:
                db.add(AssessmentSubscore(administration_id=run.id,parent_factor=parent,
                                          sub_factor=sub,unit=unit,value=value))

    injury_cases={'MC-002':('Shoulder overuse — synthetic','mid rehabilitation'),
                  'MC-004':('Ankle sprain — synthetic','late rehabilitation'),
                  'MC-007':('Hamstring injury — synthetic','return-to-training')}
    for athlete,case in athletes:
        if athlete.code not in injury_cases:continue
        injury_label,stage=injury_cases[athlete.code]
        episode=db.scalar(select(InjuryEpisode).where(InjuryEpisode.athlete_id==athlete.id,
                                                      InjuryEpisode.injury_label==injury_label))
        if not episode:
            episode=InjuryEpisode(tenant_id=tenant.id,athlete_id=athlete.id,case_id=case.id,
                                  injury_label=injury_label,start_date=(date.today()-timedelta(days=34)).isoformat(),stage=stage)
            db.add(episode);db.flush()
        existing_days={x.observed_on for x in db.scalars(select(InjuryCheckIn).where(InjuryCheckIn.episode_id==episode.id))}
        for k,offset in enumerate([28,21,14,7,0]):
            d=(date.today()-timedelta(days=offset)).isoformat()
            if d in existing_days:continue
            db.add(InjuryCheckIn(episode_id=episode.id,observed_on=d,
                                 pain=max(1,7-k),fear_of_reinjury=max(2,8-k),confidence=min(9,3+k),
                                 coping=min(9,4+k),note='Synthetic weekly rehabilitation psychology review.'))
    db.commit()


def upgrade_synthetic_demo(db):
    """Seed demonstration-only commercial listing after an upgrade, never run when demo seeding is disabled."""
    from datetime import datetime,timezone,timedelta
    from server.models import Practitioner,PractitionerProfile,AppointmentSlot
    user=db.scalar(select(Practitioner).where(Practitioner.email=='practitioner@modelc.test'))
    if not user:return
    if user.tier in ('plus','free'):user.tier='core'
    user.is_admin=True
    profile=db.scalar(select(PractitionerProfile).where(PractitionerProfile.practitioner_id==user.id))
    if not profile:
        db.add(PractitionerProfile(practitioner_id=user.id,qualifications='Synthetic sport psychology qualification — not verified',experience_years=8,achievements='Synthetic demonstration achievements',public_approved=True))
    has_slot=db.scalar(select(AppointmentSlot.id).where(AppointmentSlot.practitioner_id==user.id))
    if not has_slot:
        dt=(datetime.now(timezone.utc)+timedelta(days=3)).replace(hour=12,minute=0,second=0,microsecond=0)
        db.add(AppointmentSlot(practitioner_id=user.id,starts_at=dt,amount_paise=150000))
    db.commit()


def ensure_demo_subscriptions(db):
    """Give the fictional @modelc.test accounts a simulated membership. Local demo only."""
    from server.models import PractitionerSubscription
    from server.subscription_routes import PLANS
    for user in db.scalars(select(Practitioner)).all():
        if user.email.endswith('@modelc.test'):
            plan='pro' if user.tier=='pro' else 'core'
            user.tier=plan
            sub=db.scalar(select(PractitionerSubscription).where(PractitionerSubscription.practitioner_id==user.id))
            if not sub:
                db.add(PractitionerSubscription(practitioner_id=user.id,plan=plan,amount_paise=PLANS[plan],
                    status='active',test_mode=True,current_period_start=now(),current_period_end=now()+timedelta(days=30)))
    db.commit()
