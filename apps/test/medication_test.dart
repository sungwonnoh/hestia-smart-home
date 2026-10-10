import 'package:flutter_test/flutter_test.dart';

import 'package:hestia_flutter_test/core/utils/display_labels.dart';
import 'package:hestia_flutter_test/features/medication/medication_controllers.dart';
import 'package:hestia_flutter_test/models/medication.dart';
import 'package:hestia_flutter_test/repositories/mock_hestia_repository.dart';

void main() {
  final today = DateTime(2026, 10, 9);

  group('Medication 모델', () {
    test('자기 전만 있으면 식사 기준을 보내지 않는다', () {
      const m = Medication(
        name: ' 수면제 ',
        slots: [DoseSlot.bedtime],
        mealTiming: MealTiming.beforeMeal,
        days: 7,
      );
      expect(m.needsMealTiming, isFalse);
      expect(m.scheduleLabel, '자기 전');
      expect(m.toJson(), {
        'name': '수면제',
        'slots': ['BEDTIME'],
        'mealTiming': null,
        'days': 7,
        'refillRequired': false,
      });
    });

    test('시점은 하루 순서로 보내고 라벨을 만든다', () {
      const m = Medication(
        name: '혈압약',
        slots: [DoseSlot.dinner, DoseSlot.breakfast],
        mealTiming: MealTiming.afterMeal30,
        days: 30,
      );
      expect(m.toJson()['slots'], ['BREAKFAST', 'DINNER']);
      expect(m.toJson()['mealTiming'], 'AFTER_MEAL_30MIN');
      expect(m.scheduleLabel, '아침·저녁 · 식후 30분');
    });

    test('남은 일수와 처방 알림', () {
      Medication withEnd(DateTime end, {bool refill = true}) => Medication(
            name: '혈압약',
            slots: const [DoseSlot.breakfast],
            mealTiming: MealTiming.beforeMeal,
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

    test('서버 응답을 읽는다', () {
      final m = Medication.fromJson({
        'id': 'med-1',
        'name': '당뇨약',
        'slots': ['BREAKFAST', 'LUNCH'],
        'mealTiming': 'BEFORE_MEAL',
        'days': 14,
        'startDate': '2026-10-09',
        'endDate': '2026-10-22',
        'refillRequired': true,
      });
      expect(m.slots, [DoseSlot.breakfast, DoseSlot.lunch]);
      expect(m.mealTiming, MealTiming.beforeMeal);
      expect(m.startDate, DateTime(2026, 10, 9));
      expect(m.endDate, DateTime(2026, 10, 22));
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
        slots: [DoseSlot.dinner, DoseSlot.breakfast],
        mealTiming: MealTiming.afterMeal30,
        days: 30,
      ));
      expect(saved.id, isNotEmpty);
      expect(saved.slots, [DoseSlot.breakfast, DoseSlot.dinner]);
      expect(saved.startDate, today);
      expect(saved.endDate, DateTime(2026, 11, 7));

      final updated = await repo.updateMedication(
          saved.copyWith(slots: [DoseSlot.bedtime], days: 7));
      expect(updated.mealTiming, isNull);
      expect(updated.startDate, today);
      expect(updated.endDate, DateTime(2026, 10, 15));
      expect(await repo.getMedications(), hasLength(1));

      await repo.deleteMedication(saved.id);
      expect(await repo.getMedications(), isEmpty);
    });

    test('설정 초기화하면 복약도 지운다', () async {
      await repo.addMedication(const Medication(
          name: '수면제', slots: [DoseSlot.bedtime], days: 7));
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

    test('식사 시점이 있으면 식사 기준 단계를 거친다', () async {
      expect(c.canProceed, isFalse);
      c.setName('혈압약');
      c.next();
      expect(c.step, MedicationStep.slots);

      c.toggleSlot(DoseSlot.breakfast);
      c.toggleSlot(DoseSlot.dinner);
      c.next();
      expect(c.step, MedicationStep.timing);
      expect(c.canProceed, isFalse);
      c.setTiming(MealTiming.afterMeal30);
      c.next();

      expect(c.step, MedicationStep.days);
      c.setDays(30);
      c.next();
      expect(c.step, MedicationStep.refill);
      c.setRefill(true);
      c.next();
      expect(c.step, MedicationStep.summary);
      expect(c.stepNumber, c.steps.length);

      final saved = await c.save();
      expect(saved, isNotNull);
      expect(saved!.mealTiming, MealTiming.afterMeal30);
      expect((await repo.getMedications()).single.name, '혈압약');
    });

    test('자기 전만 고르면 식사 기준 단계를 건너뛴다', () {
      c.setName('수면제');
      c.next();
      c.toggleSlot(DoseSlot.bedtime);
      c.next();
      expect(c.step, MedicationStep.days);
      expect(c.steps, isNot(contains(MedicationStep.timing)));

      expect(c.back(), isTrue);
      expect(c.step, MedicationStep.slots);
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

    test('수정하면 같은 id로 저장한다', () async {
      final saved = await repo.addMedication(const Medication(
        name: '혈압약',
        slots: [DoseSlot.breakfast],
        mealTiming: MealTiming.beforeMeal,
        days: 30,
      ));
      final edit = MedicationEditController(repo, initial: saved);
      expect(edit.isEditing, isTrue);
      edit.setDays(14);
      final updated = await edit.save();
      expect(updated!.id, saved.id);
      expect(updated.days, 14);
      expect(await repo.getMedications(), hasLength(1));
    });
  });
}
