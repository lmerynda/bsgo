"""Independent native evidence for stationary scanner input only.

The measured multi-threshold ship-font reader is restricted here to an exact
zero speed and full hull. It does not replace navigation fields or authorize
movement, targeting, weapons, or a stop claim after navigation.
"""
from collections.abc import Mapping
from .ship_telemetry import ShipTelemetryReader


class StationaryScanReader:
    def __init__(self, large_reader):
        self.reader = ShipTelemetryReader(large_reader)

    def read(self, image, *, selected_asteroid=False):
        values = self.reader.read(image)
        stopped = values['speed'] == 0 and values['speed'] is not None
        hull, maximum = values['hull_current'], values['hull_max']
        full = hull is not None and maximum is not None and hull > 0 and hull == maximum
        target = {}
        if selected_asteroid is True and image.size == (2560,1440):
            current, maximum_hp, reason = self.reader.ratio(image,
                tuple(self.reader.reader.data['hp_box']), thresholds=(100,120,135,140,145,150,160))
            if reason is None:
                target = {'target_current_hp':current,'target_max_hp':maximum_hp,
                    'target_identity':f'asteroid:hp:{maximum_hp}',
                    'target_ratio_verified':True}
        return {'profile': 'native-stationary-scanner-v1', 'input_scope': 'scanner_only',
            'frame_size': image.size, 'stationary_verified': stopped,
            'full_hull_verified': full, 'speed': values['speed'],
            'hull_current': hull, 'hull_max': maximum,
            'reason': 'stationary_scanner_ready' if stopped and full else
                'stationary_speed_not_verified' if not stopped else 'full_hull_not_verified',
            **target}


def _evidence(snapshot, now):
    field = snapshot.fields.get('stationary_scan_evidence') if snapshot else None
    if (not snapshot or not snapshot.healthy or snapshot.frame_size != (2560,1440)
            or not 0 <= now-snapshot.acquired_at < 2 or not field or not field.valid
            or field.observed_at != snapshot.acquired_at or not isinstance(field.value, Mapping)):
        return None
    row = field.value
    if (row.get('profile') != 'native-stationary-scanner-v1' or row.get('input_scope') != 'scanner_only'
            or tuple(row.get('frame_size', ())) != snapshot.frame_size):
        return None
    return row


def scanner_target_values(snapshot, now):
    """Passive ratio evidence can retain selection during human flight.

    It supplies no speed, hull, range or input permission. Scanner admission
    separately requires stationary_scan_values and every target/frame guard.
    """
    row = _evidence(snapshot, now)
    if row is None or row.get('target_ratio_verified') is not True:
        return None
    selected = snapshot.fields.get('asteroid_selected')
    maximum, current = row.get('target_max_hp'), row.get('target_current_hp')
    if (not selected or not selected.valid or selected.observed_at != snapshot.acquired_at
            or selected.value is not True or type(maximum) is not int or maximum <= 0
            or type(current) is not int or not 0 <= current <= maximum
            or row.get('target_identity') != f'asteroid:hp:{maximum}'):
        return None
    values = {'target_max_hp':maximum,'target_identity':row['target_identity']}
    for name, value in values.items():
        calibrated = snapshot.fields.get(name)
        if calibrated and calibrated.valid and (calibrated.observed_at != snapshot.acquired_at
                                               or calibrated.value != value):
            return None
    return values


def stationary_scan_values(snapshot, now):
    row = _evidence(snapshot, now)
    if row is None:
        return None
    if (row.get('stationary_verified') is not True or row.get('full_hull_verified') is not True
            or type(row.get('speed')) not in (int,float) or row['speed'] != 0
            or type(row.get('hull_current')) is not int or row['hull_current'] <= 0
            or type(row.get('hull_max')) is not int
            or row.get('hull_current') != row.get('hull_max')):
        return None
    values = {'speed': row['speed'], 'hull_current': row['hull_current'], 'hull_max': row['hull_max']}
    if row.get('target_ratio_verified') is True:
        target = scanner_target_values(snapshot, now)
        if target is None:
            return None
        values.update(target)
    for name in values:
        calibrated = snapshot.fields.get(name)
        if calibrated and calibrated.valid:
            if calibrated.observed_at != snapshot.acquired_at or calibrated.value != row.get(name):
                return None
    return values
