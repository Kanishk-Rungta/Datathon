"""Master (dimension) data generation.

Real Karnataka geography is used because the analytics are geographic: the 31
districts, their approximate headquarters coordinates, and a plausible station
hierarchy underneath each. Everything else — names, employees, courts — is
synthetic and clearly fictional.

Determinism is a hard requirement (plan §5.4): the same seed yields byte-identical
master data, so a demo can be reproduced and a test can assert exact counts.
"""

from __future__ import annotations

import random
import unicodedata
from dataclasses import dataclass, field
from typing import Any

# (name, latitude, longitude, urban weighting) for all 31 Karnataka districts.
KARNATAKA_DISTRICTS: list[tuple[str, float, float, float]] = [
    ("Bagalkote", 16.1810, 75.6960, 0.9),
    ("Ballari", 15.1394, 76.9214, 1.0),
    ("Belagavi", 15.8497, 74.4977, 1.2),
    ("Bengaluru City", 12.9716, 77.5946, 6.0),
    ("Bengaluru Rural", 13.2846, 77.5946, 1.1),
    ("Bidar", 17.9104, 77.5199, 0.9),
    ("Chamarajanagara", 11.9261, 76.9438, 0.7),
    ("Chikkaballapura", 13.4355, 77.7315, 0.8),
    ("Chikkamagaluru", 13.3161, 75.7720, 0.8),
    ("Chitradurga", 14.2251, 76.3980, 0.9),
    ("Dakshina Kannada", 12.8438, 75.2479, 1.6),
    ("Davanagere", 14.4644, 75.9218, 1.1),
    ("Dharwad", 15.4589, 75.0078, 1.5),
    ("Gadag", 15.4315, 75.6355, 0.7),
    ("Hassan", 13.0072, 76.0962, 0.9),
    ("Haveri", 14.7951, 75.3991, 0.8),
    ("Kalaburagi", 17.3297, 76.8343, 1.3),
    ("Kodagu", 12.3375, 75.8069, 0.6),
    ("Kolar", 13.1367, 78.1292, 0.9),
    ("Koppal", 15.3547, 76.1546, 0.7),
    ("Mandya", 12.5218, 76.8951, 0.9),
    ("Mysuru", 12.2958, 76.6394, 2.2),
    ("Raichur", 16.2076, 77.3463, 0.9),
    ("Ramanagara", 12.7217, 77.2800, 0.8),
    ("Shivamogga", 13.9299, 75.5681, 1.2),
    ("Tumakuru", 13.3392, 77.1140, 1.2),
    ("Udupi", 13.3409, 74.7421, 1.0),
    ("Uttara Kannada", 14.7935, 74.6869, 0.8),
    ("Vijayanagara", 15.2667, 76.3833, 0.7),
    ("Vijayapura", 16.8302, 75.7100, 0.9),
    ("Yadgir", 16.7700, 77.1376, 0.6),
]

STATION_SUFFIXES = [
    "Town", "Rural", "North", "South", "East", "West", "Market", "Extension",
    "Industrial Area", "Traffic", "Women", "Cyber Crime",
]

UNIT_TYPES = [
    (1, "State Headquarters", "State", 1),
    (2, "Range Office", "State", 2),
    (3, "District Headquarters", "District", 3),
    (4, "Sub-Division", "District", 4),
    (5, "Circle Office", "City", 5),
    (6, "Police Station", "City", 6),
]

RANKS = [
    (1, "Director General of Police", 1), (2, "Additional Director General", 2),
    (3, "Inspector General", 3), (4, "Deputy Inspector General", 4),
    (5, "Superintendent of Police", 5), (6, "Additional Superintendent", 6),
    (7, "Deputy Superintendent", 7), (8, "Police Inspector", 8),
    (9, "Police Sub-Inspector", 9), (10, "Assistant Sub-Inspector", 10),
    (11, "Head Constable", 11), (12, "Police Constable", 12),
]

DESIGNATIONS = [
    (1, "Station House Officer", 1), (2, "Investigating Officer", 2),
    (3, "Circle Inspector", 3), (4, "Crime Branch Officer", 4),
    (5, "Traffic Officer", 5), (6, "Cyber Crime Officer", 6),
    (7, "Beat Officer", 7), (8, "Control Room Officer", 8),
]

CASE_CATEGORIES = [(1, "FIR"), (3, "UDR"), (4, "PAR"), (8, "Zero FIR")]

GRAVITY_OFFENCES = [(1, "Heinous"), (2, "Serious"), (3, "Non-Heinous"), (4, "Minor")]

CASE_STATUSES = [
    (1, "Under Investigation"), (2, "Charge Sheeted"), (3, "Pending Trial"),
    (4, "Convicted"), (5, "Acquitted"), (6, "Closed - Undetected"),
    (7, "Closed - False"), (8, "Transferred"),
]

RELIGIONS = [(1, "Hindu"), (2, "Muslim"), (3, "Christian"), (4, "Jain"),
             (5, "Sikh"), (6, "Buddhist"), (7, "Not Stated")]

CASTES = [(1, "General"), (2, "Other Backward Class"), (3, "Scheduled Caste"),
          (4, "Scheduled Tribe"), (5, "Not Stated")]

OCCUPATIONS = [
    (1, "Agriculture"), (2, "Daily Wage Labour"), (3, "Government Employee"),
    (4, "Private Employee"), (5, "Business"), (6, "Student"), (7, "Homemaker"),
    (8, "Driver"), (9, "Unemployed"), (10, "Retired"), (11, "Self Employed"),
    (12, "Information Technology"), (13, "Not Stated"),
]

# NCRB-style major heads with sub-heads and an approximate share of registered
# crime. Shares are illustrative and are stated as such in the documentation.
CRIME_TAXONOMY: list[tuple[int, str, list[tuple[int, str, float, int]]]] = [
    (1, "Crimes Against Body", [
        (101, "Murder", 0.010, 1),
        (102, "Attempt to Murder", 0.015, 1),
        (103, "Grievous Hurt", 0.045, 2),
        (104, "Simple Hurt", 0.070, 3),
        (105, "Kidnapping and Abduction", 0.018, 1),
    ]),
    (2, "Crimes Against Property", [
        (201, "House Theft", 0.115, 2),
        (202, "Motor Vehicle Theft", 0.095, 2),
        (203, "Other Theft", 0.130, 3),
        (204, "Burglary", 0.060, 2),
        (205, "Robbery", 0.030, 1),
        (206, "Dacoity", 0.006, 1),
        (207, "Chain Snatching", 0.035, 2),
    ]),
    (3, "Crimes Against Women", [
        (301, "Assault on Women", 0.038, 1),
        (302, "Cruelty by Husband or Relatives", 0.042, 2),
        (303, "Dowry Related Offences", 0.012, 1),
    ]),
    (4, "Economic Offences", [
        (401, "Cheating", 0.062, 2),
        (402, "Criminal Breach of Trust", 0.022, 2),
        (403, "Forgery", 0.018, 2),
        (404, "Online Financial Fraud", 0.048, 2),
    ]),
    (5, "Crimes Against Public Order", [
        (501, "Rioting", 0.020, 2),
        (502, "Unlawful Assembly", 0.014, 3),
        (503, "Criminal Intimidation", 0.030, 3),
    ]),
    (6, "Special and Local Laws", [
        (601, "Narcotic Drugs and Psychotropic Substances", 0.026, 1),
        (602, "Excise Act Offences", 0.024, 3),
        (603, "Gambling", 0.015, 4),
        (604, "Arms Act Offences", 0.010, 2),
    ]),
]

ACTS: list[tuple[str, str, str]] = [
    ("BNS", "Bharatiya Nyaya Sanhita, 2023", "BNS"),
    ("IPC", "Indian Penal Code, 1860", "IPC"),
    ("NDPS", "Narcotic Drugs and Psychotropic Substances Act, 1985", "NDPS"),
    ("ARMS", "Arms Act, 1959", "Arms Act"),
    ("MVA", "Motor Vehicles Act, 1988", "MV Act"),
    ("ITA", "Information Technology Act, 2000", "IT Act"),
    ("KPA", "Karnataka Police Act, 1963", "KP Act"),
    ("EXC", "Karnataka Excise Act, 1965", "Excise Act"),
    ("DPA", "Dowry Prohibition Act, 1961", "DP Act"),
]

#: sub-head id -> [(act code, section code, section description)]
SECTION_MAP: dict[int, list[tuple[str, str, str]]] = {
    101: [("BNS", "103", "Punishment for murder"), ("IPC", "302", "Punishment for murder")],
    102: [("BNS", "109", "Attempt to murder"), ("IPC", "307", "Attempt to murder")],
    103: [("BNS", "117", "Voluntarily causing grievous hurt"), ("IPC", "325", "Grievous hurt")],
    104: [("BNS", "115", "Voluntarily causing hurt"), ("IPC", "323", "Voluntarily causing hurt")],
    105: [("BNS", "137", "Kidnapping"), ("IPC", "363", "Punishment for kidnapping")],
    201: [("BNS", "305", "Theft in dwelling house"), ("IPC", "380", "Theft in dwelling house")],
    202: [("BNS", "303", "Theft"), ("MVA", "39", "Necessity for registration")],
    203: [("BNS", "303", "Theft"), ("IPC", "379", "Punishment for theft")],
    204: [("BNS", "331", "House trespass and house breaking"), ("IPC", "457", "Lurking house trespass")],
    205: [("BNS", "309", "Robbery"), ("IPC", "392", "Punishment for robbery")],
    206: [("BNS", "310", "Dacoity"), ("IPC", "395", "Punishment for dacoity")],
    207: [("BNS", "304", "Snatching"), ("IPC", "379A", "Snatching")],
    301: [("BNS", "74", "Assault on woman with intent to outrage modesty"),
          ("IPC", "354", "Assault on woman")],
    302: [("BNS", "85", "Cruelty by husband or relatives"), ("IPC", "498A", "Cruelty")],
    303: [("DPA", "4", "Penalty for demanding dowry"), ("IPC", "304B", "Dowry death")],
    401: [("BNS", "318", "Cheating"), ("IPC", "420", "Cheating and dishonestly inducing delivery")],
    402: [("BNS", "316", "Criminal breach of trust"), ("IPC", "406", "Criminal breach of trust")],
    403: [("BNS", "336", "Forgery"), ("IPC", "465", "Punishment for forgery")],
    404: [("ITA", "66D", "Cheating by personation using computer resource"),
          ("BNS", "318", "Cheating")],
    501: [("BNS", "191", "Rioting"), ("IPC", "147", "Punishment for rioting")],
    502: [("BNS", "189", "Unlawful assembly"), ("IPC", "143", "Unlawful assembly")],
    503: [("BNS", "351", "Criminal intimidation"), ("IPC", "506", "Criminal intimidation")],
    601: [("NDPS", "20", "Contravention in relation to cannabis"),
          ("NDPS", "22", "Contravention in relation to psychotropic substances")],
    602: [("EXC", "32", "Penalty for illegal possession"), ("EXC", "34", "Penalty for illegal transport")],
    603: [("KPA", "87", "Gaming in common gaming house"), ("KPA", "88", "Penalty for gaming")],
    604: [("ARMS", "25", "Punishment for certain offences"), ("ARMS", "27", "Punishment for using arms")],
}

FIRST_NAMES_MALE = [
    "Ramesh", "Suresh", "Mahesh", "Nagaraj", "Basavaraj", "Shivakumar", "Manjunath",
    "Prakash", "Vinod", "Girish", "Santosh", "Ravi", "Anand", "Dinesh", "Naveen",
    "Chandrashekar", "Umesh", "Kiran", "Praveen", "Lokesh", "Harish", "Mallikarjun",
    "Venkatesh", "Srinivas", "Ashok", "Gopal", "Yogesh", "Bharath", "Sandeep", "Vijay",
    "Rajesh", "Mohan", "Krishna", "Ganesh", "Shankar", "Raghavendra", "Vishwanath",
    "Siddappa", "Channabasappa", "Veeresh", "Sharanappa", "Ningappa", "Hanumantha",
    "Puttaswamy", "Devaraj", "Jagadish", "Sunil", "Anil", "Arun", "Karthik", "Nithin",
    "Rakesh", "Sathish", "Vasanth", "Madhusudhan", "Chetan", "Darshan", "Guruprasad",
    "Ravindra", "Shrinivas", "Somashekar", "Thimmappa", "Vittal", "Subramanya",
    "Narayana", "Keshava", "Bhaskar", "Ashwath", "Nagesh", "Muniraju", "Byrappa",
    "Eranna", "Fakruddin", "Imran", "Riyaz", "Salim", "Nazeer", "Altaf", "Mustafa",
    "Abdul", "Ibrahim", "Joseph", "Thomas", "Vincent", "Lawrence", "Ronald",
]
FIRST_NAMES_FEMALE = [
    "Lakshmi", "Savitha", "Geetha", "Roopa", "Sunitha", "Kavitha", "Shobha",
    "Padma", "Rekha", "Ambika", "Vidya", "Nandini", "Chaitra", "Deepa", "Meena",
    "Sowmya", "Bhavya", "Pallavi", "Rashmi", "Divya", "Anitha", "Sharada",
    "Manjula", "Jayanthi", "Vasanthi", "Girija", "Shruthi", "Pooja", "Asha",
    "Usha", "Nagaratna", "Basamma", "Shantha", "Renuka", "Gangamma", "Yashoda",
    "Mamatha", "Sushma", "Prema", "Vani", "Indira", "Sarojini", "Kalpana",
    "Netravathi", "Sumangala", "Chandrakala", "Rathnamma", "Bhagyamma",
    "Fathima", "Ayesha", "Zainab", "Nasreen", "Shabana", "Mary", "Agnes", "Grace",
]
SURNAMES = [
    "Gowda", "Shetty", "Hegde", "Rao", "Patil", "Naik", "Bhat", "Reddy", "Kulkarni",
    "Desai", "Murthy", "Prasad", "Nayak", "Acharya", "Kamath", "Poojary", "Jain",
    "Swamy", "Rai", "Pai", "Kotian", "Hiremath", "Angadi", "Badiger", "Talwar",
    "Gouda", "Shetter", "Kadam", "Jadhav", "Chavan", "Salunke", "Bhandari",
    "Shastri", "Joshi", "Deshpande", "Kelkar", "Hosamani", "Mathapati", "Betageri",
    "Byrappa", "Doddamani", "Gadag", "Halakatti", "Ijeri", "Jamadar", "Karajagi",
    "Lamani", "Mudhol", "Nadagouda", "Odeyar", "Palled", "Ramanagar", "Savadi",
    "Tippanna", "Uppar", "Vaddar", "Walikar", "Yaragatti", "Ballari", "Chikkanna",
    "Dandin", "Elimane", "Ganiger", "Hunashimarad", "Inamdar", "Jakati",
    "Khan", "Sheikh", "Ansari", "Qureshi", "Pathan", "Dsouza", "Fernandes",
    "Pinto", "Rodrigues", "Lobo", "Menezes", "Saldanha", "Monteiro",
]

#: Initials are ubiquitous in Karnataka police records ("K. Ramesh Gowda" —
#: village and father's initial). They matter here for a practical reason: they
#: widen the name space to roughly 300,000 combinations, so accidental
#: collisions stay rare and entity resolution is tested on *real* variance
#: rather than on an artefact of a short name list.
INITIALS = list("ABCDGHJKLMNPRSTVY")

#: Transliteration variants injected deliberately so entity resolution has real
#: work to do (plan §6.6). Key is canonical, values are plausible variants.
NAME_VARIANTS: dict[str, list[str]] = {
    "Ramesh": ["Rameshi", "Ramesha"],
    "Suresh": ["Suresha", "Suresh Kumar"],
    "Manjunath": ["Manjunatha", "Manju Nath"],
    "Basavaraj": ["Basavaraja", "Basava Raj"],
    "Shivakumar": ["Shiva Kumar", "Sivakumar"],
    "Nagaraj": ["Nagaraja", "Naga Raj"],
    "Chandrashekar": ["Chandrashekhar", "Chandra Shekar"],
    "Mallikarjun": ["Mallikarjuna", "Malikarjun"],
    "Venkatesh": ["Venkatesha", "Venkatesh Kumar"],
    "Srinivas": ["Srinivasa", "Sreenivas"],
    "Gowda": ["Gouda", "Gowdru"],
    "Shetty": ["Setty", "Shetti"],
    "Hegde": ["Hegade", "Hegdey"],
    "Krishna": ["Krishnappa", "Krisna"],
    "Naik": ["Nayak", "Naika"],
    "Kulkarni": ["Kulkarny", "Kulakarni"],
    "Reddy": ["Reddi", "Readdy"],
    "Sheikh": ["Shaik", "Shaikh"],
    "Dsouza": ["D Souza", "DSouza"],
}


@dataclass(slots=True)
class MasterData:
    states: list[dict[str, Any]] = field(default_factory=list)
    districts: list[dict[str, Any]] = field(default_factory=list)
    unit_types: list[dict[str, Any]] = field(default_factory=list)
    units: list[dict[str, Any]] = field(default_factory=list)
    ranks: list[dict[str, Any]] = field(default_factory=list)
    designations: list[dict[str, Any]] = field(default_factory=list)
    employees: list[dict[str, Any]] = field(default_factory=list)
    courts: list[dict[str, Any]] = field(default_factory=list)
    case_categories: list[dict[str, Any]] = field(default_factory=list)
    gravity_offences: list[dict[str, Any]] = field(default_factory=list)
    case_statuses: list[dict[str, Any]] = field(default_factory=list)
    religions: list[dict[str, Any]] = field(default_factory=list)
    castes: list[dict[str, Any]] = field(default_factory=list)
    occupations: list[dict[str, Any]] = field(default_factory=list)
    crime_heads: list[dict[str, Any]] = field(default_factory=list)
    crime_sub_heads: list[dict[str, Any]] = field(default_factory=list)
    acts: list[dict[str, Any]] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    crime_head_act_sections: list[dict[str, Any]] = field(default_factory=list)

    def tables(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "curated_State": self.states,
            "curated_District": self.districts,
            "curated_UnitType": self.unit_types,
            "curated_Unit": self.units,
            "curated_Rank": self.ranks,
            "curated_Designation": self.designations,
            "curated_Employee": self.employees,
            "curated_Court": self.courts,
            "curated_CaseCategory": self.case_categories,
            "curated_GravityOffence": self.gravity_offences,
            "curated_CaseStatusMaster": self.case_statuses,
            "curated_ReligionMaster": self.religions,
            "curated_CasteMaster": self.castes,
            "curated_OccupationMaster": self.occupations,
            "curated_CrimeHead": self.crime_heads,
            "curated_CrimeSubHead": self.crime_sub_heads,
            "curated_Act": self.acts,
            "curated_Section": self.sections,
            "curated_CrimeHeadActSection": self.crime_head_act_sections,
        }


KARNATAKA_STATE_ID = 29  # census state code, used verbatim


def generate_masters(rng: random.Random, *, stations_per_district: tuple[int, int] = (4, 9)) -> MasterData:
    data = MasterData()

    data.states = [
        {"StateID": KARNATAKA_STATE_ID, "StateName": "Karnataka", "NationalityID": 1, "Active": 1},
        {"StateID": 27, "StateName": "Maharashtra", "NationalityID": 1, "Active": 1},
        {"StateID": 33, "StateName": "Tamil Nadu", "NationalityID": 1, "Active": 1},
        {"StateID": 36, "StateName": "Telangana", "NationalityID": 1, "Active": 1},
        {"StateID": 32, "StateName": "Kerala", "NationalityID": 1, "Active": 1},
        {"StateID": 28, "StateName": "Andhra Pradesh", "NationalityID": 1, "Active": 1},
    ]

    data.unit_types = [
        {"UnitTypeID": type_id, "UnitTypeName": name, "CityDistState": level,
         "Hierarchy": hierarchy, "Active": 1}
        for type_id, name, level, hierarchy in UNIT_TYPES
    ]
    data.ranks = [{"RankID": r, "RankName": n, "Hierarchy": h, "Active": 1} for r, n, h in RANKS]
    data.designations = [
        {"DesignationID": d, "DesignationName": n, "Active": 1, "SortOrder": s}
        for d, n, s in DESIGNATIONS
    ]
    data.case_categories = [
        {"CaseCategoryID": c, "LookupValue": v, "CategoryCode": c} for c, v in CASE_CATEGORIES
    ]
    data.gravity_offences = [{"GravityOffenceID": g, "LookupValue": v} for g, v in GRAVITY_OFFENCES]
    data.case_statuses = [{"CaseStatusID": s, "CaseStatusName": n} for s, n in CASE_STATUSES]
    data.religions = [{"ReligionID": r, "ReligionName": n} for r, n in RELIGIONS]
    data.castes = [{"caste_master_id": c, "caste_master_name": n} for c, n in CASTES]
    data.occupations = [{"OccupationID": o, "OccupationName": n} for o, n in OCCUPATIONS]

    # State headquarters unit sits above every district.
    data.units.append({
        "UnitID": 1000, "UnitName": "Karnataka State Police Headquarters", "TypeID": 1,
        "ParentUnit": None, "NationalityID": 1, "StateID": KARNATAKA_STATE_ID,
        "DistrictID": None, "Active": 1, "cip_latitude": None, "cip_longitude": None,
    })

    unit_id = 2000
    court_id = 5000
    for index, (name, latitude, longitude, weight) in enumerate(KARNATAKA_DISTRICTS, start=1):
        district_id = 2900 + index
        data.districts.append({
            "DistrictID": district_id, "DistrictName": name,
            "StateID": KARNATAKA_STATE_ID, "Active": 1,
            "cip_latitude": latitude, "cip_longitude": longitude, "cip_weight": weight,
        })
        headquarters_id = unit_id
        data.units.append({
            "UnitID": headquarters_id, "UnitName": f"{name} District Police Office", "TypeID": 3,
            "ParentUnit": 1000, "NationalityID": 1, "StateID": KARNATAKA_STATE_ID,
            "DistrictID": district_id, "Active": 1,
            "cip_latitude": latitude, "cip_longitude": longitude,
        })
        unit_id += 1
        station_count = rng.randint(*stations_per_district)
        station_count = max(3, int(station_count * min(2.0, max(0.7, weight))))
        for suffix in rng.sample(STATION_SUFFIXES, k=min(station_count, len(STATION_SUFFIXES))):
            station_lat = round(latitude + rng.uniform(-0.09, 0.09), 6)
            station_lon = round(longitude + rng.uniform(-0.09, 0.09), 6)
            data.units.append({
                "UnitID": unit_id, "UnitName": f"{name} {suffix} Police Station", "TypeID": 6,
                "ParentUnit": headquarters_id, "NationalityID": 1, "StateID": KARNATAKA_STATE_ID,
                "DistrictID": district_id, "Active": 1,
                "cip_latitude": station_lat, "cip_longitude": station_lon,
            })
            unit_id += 1
        for court_name in (f"{name} District and Sessions Court", f"{name} Judicial Magistrate First Class"):
            data.courts.append({
                "CourtID": court_id, "CourtName": court_name, "DistrictID": district_id,
                "StateID": KARNATAKA_STATE_ID, "Active": 1,
            })
            court_id += 1

    data.employees = _generate_employees(rng, data)
    _generate_crime_taxonomy(data)
    return data


def _generate_employees(rng: random.Random, data: MasterData) -> list[dict[str, Any]]:
    employees: list[dict[str, Any]] = []
    employee_id = 10_000
    stations = [unit for unit in data.units if unit["TypeID"] == 6]
    for station in stations:
        headcount = rng.randint(4, 8)
        for position in range(headcount):
            gender = "F" if rng.random() < 0.18 else "M"
            first = rng.choice(FIRST_NAMES_FEMALE if gender == "F" else FIRST_NAMES_MALE)
            surname = rng.choice(SURNAMES)
            rank_id = 8 if position == 0 else 9 if position == 1 else rng.choice([10, 11, 12])
            designation_id = 1 if position == 0 else 2 if position <= 2 else rng.choice([7, 8])
            birth_year = rng.randint(1968, 2000)
            employees.append({
                "EmployeeID": employee_id,
                "DistrictID": station["DistrictID"],
                "UnitID": station["UnitID"],
                "RankID": rank_id,
                "DesignationID": designation_id,
                "KGID": f"KG{employee_id:07d}",
                "FirstName": f"{first} {surname}",
                "EmployeeDOB": f"{birth_year}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
                "GenderID": 2 if gender == "F" else 1,
                "BloodGroupID": rng.randint(1, 8),
                "PhysicallyChallenged": 0,
                "AppointmentDate": f"{min(2024, birth_year + rng.randint(21, 30))}-"
                                   f"{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
            })
            employee_id += 1
    return employees


def _generate_crime_taxonomy(data: MasterData) -> None:
    sequence = 0
    for head_id, group_name, sub_heads in CRIME_TAXONOMY:
        data.crime_heads.append({"CrimeHeadID": head_id, "CrimeGroupName": group_name, "Active": 1})
        for sub_head_id, sub_name, _share, _gravity in sub_heads:
            sequence += 1
            data.crime_sub_heads.append({
                # The ER document defines CrimeSubHead without an Active flag;
                # the generator follows the source schema exactly.
                "CrimeSubHeadID": sub_head_id, "CrimeHeadID": head_id,
                "CrimeHeadName": sub_name, "SeqID": sequence,
            })

    data.acts = [
        {"ActCode": code, "ActDescription": description, "ShortName": short, "Active": 1}
        for code, description, short in ACTS
    ]

    seen_sections: set[tuple[str, str]] = set()
    for sub_head_id, entries in SECTION_MAP.items():
        head_id = next(
            head for head, _name, subs in CRIME_TAXONOMY if any(s[0] == sub_head_id for s in subs)
        )
        for act_code, section_code, section_description in entries:
            if (act_code, section_code) not in seen_sections:
                seen_sections.add((act_code, section_code))
                data.sections.append({
                    "ActCode": act_code, "SectionCode": section_code,
                    "SectionDescription": section_description, "Active": 1,
                })
            data.crime_head_act_sections.append({
                "CrimeHeadID": head_id, "ActCode": act_code, "SectionCode": section_code,
            })


def person_name(
    rng: random.Random, *, gender: str | None = None, allow_variant: bool = True
) -> tuple[str, str]:
    """Return ``(name, gender)`` in the shape Karnataka FIR records actually use.

    Roughly half of names carry one or two initials. A minority are rendered
    with a transliteration variant, which is what gives entity resolution
    something real to solve.
    """
    gender = gender or ("F" if rng.random() < 0.32 else "M")
    first = rng.choice(FIRST_NAMES_FEMALE if gender == "F" else FIRST_NAMES_MALE)
    surname = rng.choice(SURNAMES)
    if allow_variant and rng.random() < 0.18:
        first = rng.choice(NAME_VARIANTS.get(first, [first]))
        surname = rng.choice(NAME_VARIANTS.get(surname, [surname]))

    roll = rng.random()
    if roll < 0.34:
        prefix = f"{rng.choice(INITIALS)}. "
    elif roll < 0.52:
        prefix = f"{rng.choice(INITIALS)}. {rng.choice(INITIALS)}. "
    else:
        prefix = ""
    return f"{prefix}{first} {surname}", gender


def name_variants_of(rng: random.Random, name: str) -> list[str]:
    """Plausible spellings of one person's name, as different stations would record it.

    Includes dropping or adding the initial, since that is the single most
    common difference between two records for the same person.
    """
    parts = name.split()
    initials = [p for p in parts if p.endswith(".")]
    core = [p for p in parts if not p.endswith(".")]
    first, surname = (core + ["", ""])[:2]

    variants = {name}
    if initials:
        variants.add(" ".join(core))                       # initial dropped
        variants.add(f"{initials[0]} {first} {surname}")   # single initial
    else:
        variants.add(f"{rng.choice(INITIALS)}. {name}")    # initial added

    for alt in NAME_VARIANTS.get(first, []):
        variants.add(" ".join(initials + [alt, surname]).strip())
    for alt in NAME_VARIANTS.get(surname, []):
        variants.add(" ".join(initials + [first, alt]).strip())
    if len(variants) == 1 and first:
        variants.add(" ".join(initials + [first + "a", surname]).strip())
    return sorted(v for v in variants if v.strip())


def ascii_fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))
