import hashlib
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
SALT = 'innoserve2026-drugdriving'


def hash_id(master_id: int) -> str:
    return hashlib.sha256(f'{SALT}:{master_id:06d}'.encode('utf-8')).hexdigest()[:16]

handoff = pd.read_csv(BASE_DIR / 'handoff_master_population.csv')
a = pd.read_csv(BASE_DIR / 'PETsARD_Dataset_A_aligned.csv')
b = pd.read_csv(BASE_DIR / 'dataset_b_aligned.csv')

assert list(handoff.columns) == ['master_id', 'hashed_id', 'age_group', 'gender', 'drug_class', 'test_result']
assert list(a.columns) == ['master_id', 'hashed_id', 'event_date', 'event_hour', 'county', 'district', 'lat_raw', 'lon_raw', 'vehicle_type', 'is_drug_related', 'suspect_age_group', 'suspect_gender', 'casualty_count', 'prior_offense_flag']
assert list(b.columns) == ['master_id', 'hashed_id', 'test_date', 'specimen_type', 'test_result', 'positive_substance', 'drug_class', 'age_group', 'gender', 'severity_score']

assert len(handoff) == len(b)
assert len(a) == round(len(b) * 1.6)
assert list(b['master_id']) == list(range(1, len(b) + 1))
assert a['master_id'].is_unique

# A∩B: only B positives caught while driving, and they carry B's age_group/gender
both = a.merge(handoff, on='master_id')
positive_n = (handoff['test_result'] == '陽性').sum()
assert len(both) == round(positive_n * 0.20), 'A∩B size is not 20% of B positives'
assert all(both['test_result'] == '陽性'), 'A∩B contains B negatives'
assert all(both['suspect_age_group'] == both['age_group']) and all(both['suspect_gender'] == both['gender']), 'A∩B rows do not match B age_group/gender'
assert all(a['is_drug_related'] == a['master_id'].isin(both['master_id']).astype(int)), 'is_drug_related does not match A∩B membership'
MOTOR_VEHICLE_KINDS = ['機車', '小客車(含客、貨兩用)', '小貨車', '大貨車', '大客車', '曳引車', '半聯結車', '全聯結車']
assert all(both['vehicle_type'].str.split('-').str[0].isin(MOTOR_VEHICLE_KINDS)), 'A∩B contains a non-motor-vehicle party'

# hash formula validation
assert all(a['hashed_id'] == a['master_id'].map(hash_id))
assert all(b['hashed_id'] == b['master_id'].map(hash_id))

# handoff carries B's values
assert handoff[['age_group', 'gender', 'drug_class', 'test_result']].equals(b[['age_group', 'gender', 'drug_class', 'test_result']])

# B internal consistency
SUBSTANCE_CLASS = {'嗎啡': '一級', '安非他命': '二級', '甲基安非他命': '二級', 'MDMA': '二級', '愷他命': '三級', '未檢出': '無'}
CLASS_SEVERITY = {'一級': 3, '二級': 2, '三級': 1, '無': 0}
assert all((b['test_result'] == '陽性') == (b['positive_substance'] != '未檢出')), 'test_result and positive_substance disagree'
assert all(b['drug_class'] == b['positive_substance'].map(SUBSTANCE_CLASS)), 'drug_class does not match positive_substance'
assert all(b['severity_score'] == b['drug_class'].map(CLASS_SEVERITY)), 'severity_score does not match drug_class'

print('Validation OK')
print(f'A rows={len(a)}, B rows={len(b)}, in both={len(both)}')
