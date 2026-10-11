import 'package:flutter/foundation.dart';

import '../../core/network/hestia_exception.dart';
import '../../core/state/async_controller.dart';
import '../../models/medication.dart';
import '../../repositories/hestia_repository.dart';

/// 복약 관리 목록.
class MedicationListController extends AsyncController<List<Medication>> {
  MedicationListController(this._repository);

  final HestiaRepository _repository;

  @override
  Future<List<Medication>> fetch() => _repository.getMedications();

  /// 실패하면 화면에 보여줄 메시지를 돌려준다.
  Future<String?> delete(Medication medication) async {
    try {
      await _repository.deleteMedication(medication.id);
      setData([
        for (final m in data ?? const <Medication>[])
          if (m.id != medication.id) m,
      ]);
      return null;
    } on HestiaException catch (e) {
      return e.message;
    }
  }
}

enum MedicationStep { name, type, delay, times, days, refill, summary }

/// 복약 추가·수정: 약 이름 → 언제(식후/정해진 시각) → 식후 몇 분 또는 시각 → 며칠분 → 정기 처방 → 확인.
class MedicationEditController extends ChangeNotifier {
  MedicationEditController(this._repository, {Medication? initial})
      : _initial = initial,
        _name = initial?.name ?? '',
        _type = initial?.schedule.type,
        _delay = initial?.schedule.delayMin,
        _times = {...?initial?.schedule.times},
        _days = initial?.days,
        _refill = initial?.refillRequired;

  static const namePresets = ['혈압약', '당뇨약', '고지혈증약', '관절약', '소화제', '수면제', '감기약', '비타민', '영양제'];
  static const dayPresets = [3, 5, 7, 14, 30, 60, 90];
  static const maxNameLength = 30;
  static const maxDays = 365;

  /// 정해진 시각 단계의 빠른 선택.
  static const timePresets = [
    ('아침', '08:00'),
    ('점심', '12:00'),
    ('저녁', '18:00'),
    ('자기 전', '22:00'),
  ];

  final HestiaRepository _repository;
  final Medication? _initial;

  String _name;
  ScheduleType? _type;
  int? _delay;
  final Set<String> _times;
  int? _days;
  bool? _refill;

  MedicationStep _step = MedicationStep.name;
  bool _saving = false;
  String? _error;

  bool get isEditing => _initial != null;
  MedicationStep get step => _step;
  bool get saving => _saving;
  String? get error => _error;

  String get name => _name;
  ScheduleType? get type => _type;
  int? get delay => _delay;

  /// 하루 순서로 정렬된 시각.
  List<String> get times => _times.toList()..sort();
  int? get days => _days;
  bool? get refill => _refill;

  bool get canAddTime => _times.length < MedicationSchedule.maxTimes;

  List<MedicationStep> get steps => [
        MedicationStep.name,
        MedicationStep.type,
        if (_type != ScheduleType.fixed) MedicationStep.delay,
        if (_type == ScheduleType.fixed) MedicationStep.times,
        MedicationStep.days,
        MedicationStep.refill,
        MedicationStep.summary,
      ];

  /// 1부터 시작하는 현재 단계 번호.
  int get stepNumber => steps.indexOf(_step) + 1;

  bool get isFirstStep => _step == MedicationStep.name;
  bool get isLastStep => _step == MedicationStep.summary;

  bool get canProceed => switch (_step) {
        MedicationStep.name =>
          _name.trim().isNotEmpty && _name.trim().length <= maxNameLength,
        MedicationStep.type => _type != null,
        MedicationStep.delay => _delay != null,
        MedicationStep.times => _times.isNotEmpty,
        MedicationStep.days => _days != null,
        MedicationStep.refill => _refill != null,
        MedicationStep.summary => !_saving,
      };

  void setName(String value) {
    _name = value;
    notifyListeners();
  }

  void setType(ScheduleType value) {
    _type = value;
    notifyListeners();
  }

  void setDelay(int minutes) {
    _delay = minutes;
    notifyListeners();
  }

  /// 있으면 빼고, 없으면 더한다 (하루 최대 [MedicationSchedule.maxTimes]개).
  void toggleTime(String time) {
    if (!_times.remove(time) && canAddTime) _times.add(time);
    notifyListeners();
  }

  void setDays(int value) {
    _days = value.clamp(1, maxDays);
    notifyListeners();
  }

  /// [−]/[+] 버튼. 아직 고르지 않았으면 7일에서 시작한다.
  void addDays(int delta) => setDays((_days ?? 7) + delta);

  void setRefill(bool value) {
    _refill = value;
    notifyListeners();
  }

  void next() {
    if (!canProceed || isLastStep) return;
    _step = steps[steps.indexOf(_step) + 1];
    _error = null;
    notifyListeners();
  }

  /// 첫 단계면 false (화면을 닫는다).
  bool back() {
    if (isFirstStep) return false;
    _step = steps[steps.indexOf(_step) - 1];
    _error = null;
    notifyListeners();
    return true;
  }

  /// 확인 화면에서 특정 단계로 돌아가 고친다.
  void goTo(MedicationStep step) {
    if (!steps.contains(step)) return;
    _step = step;
    notifyListeners();
  }

  MedicationSchedule get schedule => _type == ScheduleType.fixed
      ? MedicationSchedule.fixed(times)
      : MedicationSchedule.afterMeal(delayMin: _delay ?? 30);

  Medication build() => Medication(
        id: _initial?.id ?? '',
        name: _name.trim(),
        schedule: schedule,
        days: _days ?? 1,
        startDate: _initial?.startDate,
        endDate: _initial?.endDate,
        refillRequired: _refill ?? false,
      );

  /// 성공하면 저장된 일정, 실패하면 null ([error]에 사유).
  Future<Medication?> save() async {
    if (_saving) return null;
    _saving = true;
    _error = null;
    notifyListeners();
    try {
      final draft = build();
      return isEditing
          ? await _repository.updateMedication(draft)
          : await _repository.addMedication(draft);
    } on HestiaException catch (e) {
      _error = e.message;
      return null;
    } finally {
      _saving = false;
      notifyListeners();
    }
  }
}
