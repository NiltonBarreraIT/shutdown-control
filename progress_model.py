"""Shared OT -> OP -> report progress calculations for Shutdown Control."""

import datetime as dt
from collections import defaultdict
from zoneinfo import ZoneInfo


CHILE = ZoneInfo('America/Santiago')


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def moment(value, end_of_day=False):
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = dt.datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        try:
            parsed_date = dt.date.fromisoformat(text[:10])
            parsed = dt.datetime.combine(
                parsed_date, dt.time.max if end_of_day else dt.time.min)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=CHILE)
    return parsed.astimezone(CHILE)


def report_moment(report):
    return (moment(report.get('submitted_at')) or
            moment(report.get('report_date'), end_of_day=True) or
            moment(report.get('start_at')))


def planned_progress(task, cutoff):
    start, finish = moment(task.get('start_at')), moment(task.get('finish_at'))
    if not start or not finish or finish <= start:
        return None
    if cutoff <= start:
        return 0.0
    if cutoff >= finish:
        return 100.0
    return max(0.0, min(100.0, (cutoff - start).total_seconds() /
                              (finish - start).total_seconds() * 100.0))


def positive(value):
    parsed = number(value)
    return parsed if parsed is not None and parsed > 0 else 0.0


def choose_method(items):
    if items and all(item['planned_hh'] > 0 for item in items):
        return 'planned_hh'
    if items and all(item['duration_hours'] > 0 for item in items):
        return 'duration'
    return 'uniform'


def item_weight(item, method):
    if method == 'planned_hh':
        return item['planned_hh']
    if method == 'duration':
        return item['duration_hours']
    return 1.0


def weighted(items, field, method):
    total = sum(item_weight(item, method) for item in items)
    return (sum(item[field] * item_weight(item, method) for item in items) / total
            if total else 0.0)


def consolidate(tasks, reports, cutoff):
    task_groups, report_groups = defaultdict(list), defaultdict(list)
    for task in tasks:
        key = (str(task.get('ot') or '').strip(), str(task.get('activity') or '').strip())
        if all(key):
            task_groups[key].append(task)
    seen = set()
    for report in sorted(reports, key=lambda item: (report_moment(item) or dt.datetime.min.replace(tzinfo=CHILE),
                                                     str(item.get('id') or ''))):
        report_id = str(report.get('id') or '').strip()
        if not report_id or report_id in seen:
            continue
        seen.add(report_id)
        key = (str(report.get('ot') or '').strip(), str(report.get('activity') or '').strip())
        progress = number(report.get('progress'))
        event_at = report_moment(report)
        if all(key) and progress is not None and 0 <= progress <= 100 and event_at and event_at <= cutoff:
            report_groups[key].append(report)

    operations = []
    for (ot, op), op_tasks in task_groups.items():
        task_items = []
        for task in op_tasks:
            planned = planned_progress(task, cutoff)
            task_items.append({
                'planned': planned if planned is not None else 0.0,
                'planned_hh': positive(task.get('planned_hh')),
                'duration_hours': positive(task.get('duration_hours')),
            })
        task_method = choose_method(task_items)
        events = report_groups.get((ot, op), [])
        raw_actual = sum(number(report.get('progress')) or 0.0 for report in events)
        executed_hh = sum(positive(report.get('workers')) * positive(report.get('hours'))
                          for report in events)
        operations.append({
            'ot': ot, 'op': op,
            'name': next((str(task.get('name') or '') for task in op_tasks if task.get('name')), ''),
            'area': next((str(task.get('area') or '') for task in op_tasks if task.get('area')), ''),
            'shift': next((str(task.get('shift') or '') for task in op_tasks if task.get('shift')), ''),
            'planned': weighted(task_items, 'planned', task_method),
            'actual': min(100.0, raw_actual),
            'raw_actual': raw_actual,
            'planned_hh': sum(item['planned_hh'] for item in task_items),
            'duration_hours': sum(item['duration_hours'] for item in task_items),
            'executed_hh': executed_hh,
            'report_count': len(events),
            'reports': events,
            'over_100': raw_actual > 100.0,
        })
    return operations


def summarize(tasks, reports, cutoff=None):
    cutoff = cutoff or dt.datetime.now(CHILE)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=CHILE)
    else:
        cutoff = cutoff.astimezone(CHILE)
    operations = consolidate(tasks, reports, cutoff)
    method = choose_method(operations)
    planned = weighted(operations, 'planned', method)
    actual = weighted(operations, 'actual', method)
    hh_coverage = bool(operations) and all(item['planned_hh'] > 0 for item in operations)
    estimated_bhp = weighted(operations, 'actual', 'planned_hh') if hh_coverage else None
    ots = defaultdict(list)
    for operation in operations:
        ots[operation['ot']].append(operation)
    work_orders = []
    for ot, children in ots.items():
        ot_method = choose_method(children)
        work_orders.append({
            'ot': ot, 'progress': weighted(children, 'actual', ot_method),
            'planned': weighted(children, 'planned', ot_method),
            'method': ot_method, 'operations': len(children),
        })
    areas = []
    by_area = defaultdict(list)
    for operation in operations:
        by_area[operation['area'] or 'Sin área'].append(operation)
    for area, children in by_area.items():
        area_method = choose_method(children)
        areas.append({'area': area, 'planned': weighted(children, 'planned', area_method),
                      'actual': weighted(children, 'actual', area_method),
                      'variance': weighted(children, 'actual', area_method) -
                                  weighted(children, 'planned', area_method),
                      'method': area_method, 'operations': len(children)})
    planned_hh = sum(item['planned_hh'] for item in operations)
    executed_hh = sum(item['executed_hh'] for item in operations)
    return {
        'cutoff': cutoff.isoformat(), 'method': method,
        'planned': planned, 'actual': actual, 'estimated_bhp': estimated_bhp,
        'variance': actual - planned,
        'spi': actual / planned if planned > 0 else None,
        'planned_hh': planned_hh, 'executed_hh': executed_hh,
        'pending_hh': max(0.0, planned_hh - executed_hh),
        'counts': {
            'ots': len(work_orders), 'ops': len(operations),
            'completed_ops': sum(item['actual'] >= 100 for item in operations),
            'delayed_ops': sum(item['planned'] - item['actual'] > 5 for item in operations),
            'over_100_ops': sum(item['over_100'] for item in operations),
        },
        'operations': operations, 'work_orders': work_orders,
        'areas': sorted(areas, key=lambda item: item['variance']),
    }


def curve(tasks, reports, cutoff=None):
    cutoff = cutoff or dt.datetime.now(CHILE)
    starts = [moment(task.get('start_at')) for task in tasks]
    starts = [item for item in starts if item]
    if not starts:
        return []
    cursor = min(starts).replace(hour=8, minute=0, second=0, microsecond=0)
    points = []
    while cursor < cutoff:
        summary = summarize(tasks, reports, cursor)
        points.append({key: summary[key] for key in
                       ('cutoff', 'planned', 'actual', 'estimated_bhp', 'variance', 'spi')})
        cursor += dt.timedelta(days=1)
    summary = summarize(tasks, reports, cutoff)
    points.append({key: summary[key] for key in
                   ('cutoff', 'planned', 'actual', 'estimated_bhp', 'variance', 'spi')})
    finishes = [moment(task.get('finish_at')) for task in tasks]
    finishes = [item for item in finishes if item]
    finish = max(finishes) if finishes else cutoff
    cursor = (cutoff + dt.timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    while cursor <= finish:
        future = summarize(tasks, reports, cursor)
        points.append({'cutoff': cursor.isoformat(), 'planned': future['planned'],
                       'actual': None, 'estimated_bhp': None,
                       'variance': None, 'spi': None})
        cursor += dt.timedelta(days=1)
    return points


def projections(points, planned_finish=None):
    actual_points = [point for point in points if point.get('actual') is not None]
    if not actual_points:
        return {'optimistic': None, 'base': None, 'conservative': None,
                'maximum_attainable': None, 'assumption': 'Sin historial suficiente'}
    last = actual_points[-1]
    daily = [max(0.0, actual_points[index]['actual'] - actual_points[index - 1]['actual'])
             for index in range(1, len(actual_points))]
    recent = [item for item in daily[-3:] if item > 0]
    base_rate = sum(recent) / len(recent) if recent else 0.0
    cutoff = moment(last['cutoff'])

    def projected(rate):
        if not cutoff or rate <= 0:
            return None
        days = max(0.0, 100.0 - last['actual']) / rate
        return (cutoff + dt.timedelta(days=days)).isoformat()

    finish = moment(planned_finish)
    remaining_days = max(0.0, (finish - cutoff).total_seconds() / 86400) if finish and cutoff else 0.0
    maximum = min(100.0, last['actual'] + base_rate * remaining_days) if base_rate else last['actual']
    return {
        'optimistic': projected(base_rate * 1.2),
        'base': projected(base_rate),
        'conservative': projected(base_rate * 0.8),
        'maximum_attainable': maximum,
        'daily_rate': base_rate,
        'assumption': 'Promedio de incrementos positivos de los últimos 3 cortes; ±20% para escenarios',
    }
