import 'package:flutter_test/flutter_test.dart';

import 'package:hestia_flutter_test/core/utils/display_labels.dart';
import 'package:hestia_flutter_test/features/medication/medication_controllers.dart';
import 'package:hestia_flutter_test/models/medication.dart';
import 'package:hestia_flutter_test/repositories/mock_hestia_repository.dart';

void main() {
  final today = DateTime(2026, 10, 9);

  group('Medication 모델', () {
    test('식후: delayMin만 보내고 끼니마다라고 표시한다', () {
      const m = Medication(
        name: ' 감기약 ',
        schedule: MedicationSchedule.afterMeal(delayMin: 30),
        days: 5,
      );
      expect(m.scheduleLabel, '식후 30분 · 끼니마다');
      expect(m.toJson(), {
        'name': '감기약',
        'schedule': {'type': 'AFTER_MEAL', 'delayMin': 30},
        'days': 5,
        'refillRequired': false,
      });
      expect(
        const MedicationSchedule.afterMeal(delayMin: 0).label,
        '식사 직후 · 끼니마다',
      );
    });

    test('정해진 시각: 하루 순서로 정렬하고 중복을 뺀다', () {
      const m = Medication(
        name: '혈압약',
        schedule: MedicationSchedule.fixed(['20:00', '08:00', '20:00']),
        days: 30,
      );
      expect(m.toJson()['schedule'], {
        'type': 'FIXED',
        'times': ['08:00', '20:00'],
      });
      expect(m.scheduleLabel, '08:00 · 20:00');
      expect(MedicationSchedule.formatTime(8, 5), '08:05');
    });

    test('남은 일수와 처방 알림', () {
      Medication withEnd(DateTime end, {bool refill = true}) => Medication(
            name: '혈압약',
            schedule: const MedicationSchedule.fixed(['08:00']),
            days: 30,
            endDate: end,
            refillRequired: refill,
          );

      expect(withEnd(DateTime(2026, 10, 20)).remainingDays(today), 12);
      expect(withEnd(DateTime(2026, 10, 11)).remainingDays(today), 3);
      expect(withEnd(DateTime(2026, 10, 11)).needsRefill(today), isTrue);
      expect(withEnd(DateTime(2026, 10, 11), refill: false).needsRefill(today),
          isFalse);
      expect(withEnd(DateTime(2026, 10, 1)).remainingDays(today), 0);

      expect(MedicationLabels.period(withEnd(DateTime(2026, 10, 20)), today),
          ('12일 남음', false));
      expect(MedicationLabels.period(withEnd(DateTime(2026, 10, 11)), today),
          ('3일 남음 · 처방 확인', true));
      expect(MedicationLabels.period(withEnd(DateTime(2026, 10, 1)), today),
          ('복용 기간 끝 · 처방 확인', true));
    });

    test('서버 응답을 읽는다 (쓰지 않는 값은 null / [])', () {
      final fixed = Medication.fromJson({
        'id': 'med-1',
        'name': '당뇨약',
        'schedule': {'type': 'FIXED', 'delayMin': null, 'times': ['07:30', '18:30']},
        'days': 14,
        'startDate': '2026-10-09',
        'endDate': '2026-10-22',
        'refillRequired': true,
      });
      expect(fixed.schedule,
          const MedicationSchedule.fixed(['07:30', '18:30']));
      expect(fixed.startDate, DateTime(2026, 10, 9));
      expect(fixed.endDate, DateTime(2026, 10, 22));

      final after = Medication.fromJson({
        'id': 'med-2',
        'name': '감기약',
        'schedule': {'type': 'AFTER_MEAL', 'delayMin': 0, 'times': []},
        'days': 5,
      });
      expect(after.schedule, const MedicationSchedule.afterMeal(delayMin: 0));
    });

    test('시간대 아이콘', () {
      expect(MedicationLabels.timeIcon('08:00'),
          MedicationLabels.timeIcon('06:30'));
      expect(MedicationLabels.timeIcon('22:00'),
          isNot(MedicationLabels.timeIcon('08:00')));
    });
  });

  group('MockHestiaRepository 복약', () {
    late MockHestiaRepository repo;

    setUp(() {
      repo = MockHestiaRepository(latency: Duration.zero, clock: () => today);
    });

    test('추가·수정·삭제', () async {
      expect(await repo.getMedications(), isEmpty);

      final saved = await repo.addMedication(const Medication(
        name: '혈압약',
        schedule: MedicationSchedule.fixed(['20:00', '08:00']),
        days: 30,
      ));
      expect(saved.id, isNotEmpty);
      expect(saved.schedule.times, ['08:00', '20:00']);
      expect(saved.startDate, today);
      expect(saved.endDate, DateTime(2026, 11, 7));

      final updated = await repo.updateMedication(saved.copyWith(
          schedule: const MedicationSchedule.afterMeal(delayMin: 0), days: 7));
      expect(updated.schedule.type, ScheduleType.afterMeal);
      expect(updated.schedule.delayMin, 0);
      expect(updated.startDate, today);
      expect(updated.endDate, DateTime(2026, 10, 15));
      expect(await repo.getMedications(), hasLength(1));

      await repo.deleteMedication(saved.id);
      expect(await repo.getMedications(), isEmpty);
    });

    test('설정 초기화하면 복약도 지운다', () async {
      await repo.addMedication(const Medication(
          name: '수면제',
          schedule: MedicationSchedule.fixed(['22:00']),
          days: 7));
      await repo.resetSetup();
      expect(await repo.getMedications(), isEmpty);
    });
  });

  group('MedicationEditController', () {
    late MockHestiaRepository repo;
    late MedicationEditController c;

    setUp(() {
      repo = MockHestiaRepository(latency: Duration.zero, clock: () => today);
      c = MedicationEditController(repo);
    });

    Future<Medication?> finish(MedicationEditController c) async {
      expect(c.step, MedicationStep.days);
      c.setDays(30);
      c.next();
      expect(c.step, MedicationStep.refill);
      c.setRefill(true);
      c.next();
      expect(c.step, MedicationStep.summary);
      expect(c.stepNumber, c.steps.length);
      return c.save();
    }

    test('식후: 식사 후 단계를 거치고 시각 단계는 없다', () async {
      expect(c.canProceed, isFalse);
      c.setName('감기약');
      c.next();
      expect(c.step, MedicationStep.type);
      expect(c.canProceed, isFalse);

      c.setType(ScheduleType.afterMeal);
      c.next();
      expect(c.step, MedicationStep.delay);
      expect(c.steps, isNot(contains(MedicationStep.times)));
      expect(c.canProceed, isFalse);
      c.setDelay(30);
      c.next();

      final saved = await finish(c);
      expect(saved!.schedule, const MedicationSchedule.afterMeal(delayMin: 30));
      expect((await repo.getMedications()).single.name, '감기약');
    });

    test('정해진 시각: 시각 단계를 거치고 식사 후 단계는 없다', () async {
      c.setName('혈압약');
      c.next();
      c.setType(ScheduleType.fixed);
      c.next();
      expect(c.step, MedicationStep.times);
      expect(c.steps, isNot(contains(MedicationStep.delay)));
      expect(c.canProceed, isFalse);

      c.toggleTime('20:00');
      c.toggleTime('08:00');
      c.toggleTime('12:00');
      c.toggleTime('12:00'); // 다시 누르면 빠진다
      expect(c.times, ['08:00', '20:00']);
      c.next();

      final saved = await finish(c);
      expect(saved!.schedule, const MedicationSchedule.fixed(['08:00', '20:00']));

      expect(c.back(), isTrue);
      expect(c.back(), isTrue);
      expect(c.back(), isTrue);
      expect(c.step, MedicationStep.times);
    });

    test('시각은 하루 6개까지', () {
      for (final t in ['06:00', '08:00', '10:00', '12:00', '14:00', '16:00']) {
        c.toggleTime(t);
      }
      expect(c.canAddTime, isFalse);
      c.toggleTime('18:00');
      expect(c.times, hasLength(MedicationSchedule.maxTimes));
      expect(c.times, isNot(contains('18:00')));
    });

    test('일수 조정은 1~365일 안에서', () {
      c.addDays(-100);
      expect(c.days, 1);
      c.setDays(400);
      expect(c.days, 365);
    });

    test('첫 단계에서 이전은 화면을 닫는다', () {
      expect(c.back(), isFalse);
    });

    test('수정하면 같은 id로 저장하고 기존 시각을 이어받는다', () async {
      final saved = await repo.addMedication(const Medication(
        name: '혈압약',
        schedule: MedicationSchedule.fixed(['08:00', '20:00']),
        days: 30,
      ));
      final edit = MedicationEditController(repo, initial: saved);
      expect(edit.isEditing, isTrue);
      expect(edit.type, ScheduleType.fixed);
      expect(edit.times, ['08:00', '20:00']);
      edit.setDays(14);
      final updated = await edit.save();
      expect(updated!.id, saved.id);
      expect(updated.days, 14);
      expect(updated.schedule, saved.schedule);
      expect(await repo.getMedications(), hasLength(1));
    });
  });
}
