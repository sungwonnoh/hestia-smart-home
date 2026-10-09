import 'package:flutter/material.dart';

import '../../app/hestia_scope.dart';
import '../../app/theme.dart';
import '../../core/utils/display_labels.dart';
import '../../models/medication.dart';
import 'medication_controllers.dart';

/// 복약 추가·수정. 약 이름 말고는 모두 버튼으로 고른다.
///
/// 저장하면 저장된 [Medication]을 돌려주며 닫힌다.
class MedicationEditPage extends StatefulWidget {
  const MedicationEditPage({super.key, this.initial});

  /// 수정할 일정. null이면 새로 추가한다.
  final Medication? initial;

  @override
  State<MedicationEditPage> createState() => _MedicationEditPageState();
}

class _MedicationEditPageState extends State<MedicationEditPage> {
  late final MedicationEditController _controller = MedicationEditController(
    HestiaScope.of(context).repository,
    initial: widget.initial,
  );
  late final TextEditingController _nameField =
      TextEditingController(text: widget.initial?.name ?? '');

  @override
  void dispose() {
    _controller.dispose();
    _nameField.dispose();
    super.dispose();
  }

  void _back() {
    if (!_controller.back()) Navigator.of(context).pop();
  }

  void _pickName(String name) {
    _nameField.text = name;
    _controller.setName(name);
  }

  Future<void> _save() async {
    final saved = await _controller.save();
    if (!mounted) return;
    if (saved != null) {
      Navigator.of(context).pop(saved);
    } else if (_controller.error != null) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('저장하지 못했습니다. ${_controller.error}')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(_controller.isEditing ? '복약 수정' : '복약 추가'),
      ),
      body: SafeArea(
        child: ListenableBuilder(
          listenable: _controller,
          builder: (context, _) {
            final c = _controller;
            final (title, subtitle) = _titles[c.step]!;
            return Column(
              children: [
                _StepHeader(
                  number: c.stepNumber,
                  total: c.steps.length,
                  title: title,
                  subtitle: subtitle,
                ),
                Expanded(
                  child: Scrollbar(
                    child: SingleChildScrollView(
                      padding: const EdgeInsets.fromLTRB(24, 12, 24, 24),
                      child: _body(c),
                    ),
                  ),
                ),
                _BottomBar(
                  backLabel: c.isFirstStep ? '취소' : '이전',
                  onBack: c.saving ? null : _back,
                  nextLabel: c.isLastStep ? '저장' : '다음',
                  onNext: !c.canProceed
                      ? null
                      : c.isLastStep
                          ? () => _save()
                          : c.next,
                  busy: c.saving,
                ),
              ],
            );
          },
        ),
      ),
    );
  }

  static const _titles = {
    MedicationStep.name: ('무슨 약인가요?', '자주 드시는 약을 고르거나 직접 입력하세요.'),
    MedicationStep.slots: (
      '하루 중 언제 드세요?',
      '아침·점심·저녁·자기 전 중 드시는 때를 모두 골라주세요.',
    ),
    MedicationStep.timing: (
      '식사 전후 언제 드세요?',
      '약 봉투에 적힌 대로 골라주세요. (예: 식후 30분)',
    ),
    MedicationStep.days: ('며칠분 약인가요?', '버튼으로 고르고 −/+로 조정하세요.'),
    MedicationStep.refill: ('주기적으로 처방받는 약인가요?', '약이 떨어지기 전에 알려드려요.'),
    MedicationStep.summary: ('이대로 저장할까요?', '항목을 눌러 고칠 수 있어요.'),
  };

  Widget _body(MedicationEditController c) => switch (c.step) {
        MedicationStep.name => _NameStep(
            controller: c,
            field: _nameField,
            onPick: _pickName,
          ),
        MedicationStep.slots => _ChoiceWrap(
            children: [
              for (final slot in DoseSlot.values)
                _ChoiceButton(
                  icon: MedicationLabels.slotIcon(slot),
                  label: slot.label,
                  selected: c.slots.contains(slot),
                  onTap: () => c.toggleSlot(slot),
                ),
            ],
          ),
        MedicationStep.timing => _ChoiceWrap(
            children: [
              for (final timing in MealTiming.values)
                _ChoiceButton(
                  icon: MedicationLabels.timingIcon(timing),
                  label: timing.label,
                  selected: c.timing == timing,
                  onTap: () => c.setTiming(timing),
                ),
            ],
          ),
        MedicationStep.days => _DaysStep(controller: c),
        MedicationStep.refill => _ChoiceWrap(
            children: [
              _ChoiceButton(
                icon: Icons.event_repeat_rounded,
                label: '예',
                caption: '남은 날이 적으면 알려드려요',
                selected: c.refill == true,
                onTap: () => c.setRefill(true),
              ),
              _ChoiceButton(
                icon: Icons.event_busy_rounded,
                label: '아니요',
                caption: '이번 약만 먹어요',
                selected: c.refill == false,
                onTap: () => c.setRefill(false),
              ),
            ],
          ),
        MedicationStep.summary => _Summary(controller: c),
      };
}

// ------------------------------------------------------------------ steps

class _NameStep extends StatelessWidget {
  const _NameStep({
    required this.controller,
    required this.field,
    required this.onPick,
  });

  final MedicationEditController controller;
  final TextEditingController field;
  final ValueChanged<String> onPick;

  @override
  Widget build(BuildContext context) {
    final name = controller.name.trim();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _ChoiceWrap(
          children: [
            for (final preset in MedicationEditController.namePresets)
              _ChoiceButton(
                icon: Icons.medication_rounded,
                label: preset,
                selected: name == preset,
                onTap: () => onPick(preset),
              ),
          ],
        ),
        const SizedBox(height: 24),
        TextField(
          controller: field,
          onChanged: controller.setName,
          maxLength: MedicationEditController.maxNameLength,
          style: const TextStyle(fontSize: 20),
          decoration: const InputDecoration(
            labelText: '직접 입력',
            hintText: '예: 갑상선약',
            border: OutlineInputBorder(),
            prefixIcon: Icon(Icons.edit_rounded),
          ),
        ),
      ],
    );
  }
}

class _DaysStep extends StatelessWidget {
  const _DaysStep({required this.controller});

  final MedicationEditController controller;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final days = controller.days;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _ChoiceWrap(
          children: [
            for (final preset in MedicationEditController.dayPresets)
              _ChoiceButton(
                label: '$preset일',
                selected: days == preset,
                onTap: () => controller.setDays(preset),
              ),
          ],
        ),
        const SizedBox(height: 28),
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            _StepperButton(
              icon: Icons.remove_rounded,
              onTap: days == null || days > 1
                  ? () => controller.addDays(-1)
                  : null,
            ),
            SizedBox(
              width: 160,
              child: Text(
                days == null ? '- 일' : '$days일',
                textAlign: TextAlign.center,
                style: theme.textTheme.headlineMedium
                    ?.copyWith(fontWeight: FontWeight.w700),
              ),
            ),
            _StepperButton(
              icon: Icons.add_rounded,
              onTap: days == null || days < MedicationEditController.maxDays
                  ? () => controller.addDays(1)
                  : null,
            ),
          ],
        ),
      ],
    );
  }
}

class _Summary extends StatelessWidget {
  const _Summary({required this.controller});

  final MedicationEditController controller;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final m = controller.build();
    final rows = [
      (MedicationStep.name, '약 이름', m.name),
      (MedicationStep.slots, '하루 언제', m.slots.map((s) => s.label).join(' · ')),
      if (m.needsMealTiming)
        (MedicationStep.timing, '식사 기준', m.mealTiming?.label ?? '-'),
      (MedicationStep.days, '기간', '${m.days}일분'),
      (MedicationStep.refill, '정기 처방', m.refillRequired ? '예' : '아니요'),
    ];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final (step, label, value) in rows)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: Material(
              color: theme.colorScheme.surfaceContainerLow,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(HestiaSizes.radius),
              ),
              clipBehavior: Clip.antiAlias,
              child: InkWell(
                onTap: controller.saving ? null : () => controller.goTo(step),
                child: Padding(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
                  child: Row(
                    children: [
                      SizedBox(
                        width: 110,
                        child: Text(label, style: theme.textTheme.bodyLarge),
                      ),
                      Expanded(
                        child: Text(value, style: theme.textTheme.titleMedium),
                      ),
                      Text(
                        '수정',
                        style: theme.textTheme.titleSmall
                            ?.copyWith(color: theme.colorScheme.primary),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        const SizedBox(height: 8),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(Icons.info_outline_rounded, color: theme.colorScheme.outline),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                '식사를 감지하면 약 드실 때 알려드려요. '
                '식사가 감지되지 않으면 평소 식사 시간에 알려드려요.',
                style: theme.textTheme.bodyMedium,
              ),
            ),
          ],
        ),
      ],
    );
  }
}

// ------------------------------------------------------------------ widgets

class _StepHeader extends StatelessWidget {
  const _StepHeader({
    required this.number,
    required this.total,
    required this.title,
    required this.subtitle,
  });

  final int number;
  final int total;
  final String title;
  final String subtitle;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(24, 12, 24, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text(
                '$number / $total',
                style: theme.textTheme.labelLarge?.copyWith(
                  color: theme.colorScheme.primary,
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(4),
                  child: LinearProgressIndicator(
                    value: number / total,
                    minHeight: 8,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          Text(title, style: theme.textTheme.headlineSmall),
          const SizedBox(height: 4),
          Text(subtitle, style: theme.textTheme.bodyLarge),
        ],
      ),
    );
  }
}

class _ChoiceWrap extends StatelessWidget {
  const _ChoiceWrap({required this.children});

  final List<Widget> children;

  @override
  Widget build(BuildContext context) =>
      Wrap(spacing: 12, runSpacing: 12, children: children);
}

/// 큰 선택 버튼. 고른 것은 강조색과 체크 표시로 구분한다.
class _ChoiceButton extends StatelessWidget {
  const _ChoiceButton({
    required this.label,
    required this.selected,
    required this.onTap,
    this.icon,
    this.caption,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;
  final IconData? icon;
  final String? caption;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final fg = selected ? scheme.onPrimaryContainer : scheme.onSurfaceVariant;
    return Semantics(
      selected: selected,
      button: true,
      child: Material(
        color: selected ? scheme.primaryContainer : scheme.surfaceContainerLow,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(HestiaSizes.radius),
          side: BorderSide(
            color: selected ? scheme.primary : scheme.outlineVariant,
            width: 2,
          ),
        ),
        clipBehavior: Clip.antiAlias,
        child: InkWell(
          onTap: onTap,
          child: ConstrainedBox(
            constraints: const BoxConstraints(
              minWidth: 150,
              minHeight: HestiaSizes.primaryButtonHeight + 8,
            ),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 12),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  if (icon != null) ...[
                    Icon(icon, size: 28, color: fg),
                    const SizedBox(width: 10),
                  ],
                  Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        label,
                        style: TextStyle(
                          fontSize: 20,
                          fontWeight: FontWeight.w700,
                          color: fg,
                        ),
                      ),
                      if (caption != null)
                        Text(caption!,
                            style: TextStyle(fontSize: 14, color: fg)),
                    ],
                  ),
                  const SizedBox(width: 10),
                  Icon(
                    selected
                        ? Icons.check_circle_rounded
                        : Icons.radio_button_unchecked_rounded,
                    color: selected ? scheme.primary : scheme.outline,
                    size: 24,
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _StepperButton extends StatelessWidget {
  const _StepperButton({required this.icon, required this.onTap});

  final IconData icon;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return SizedBox.square(
      dimension: HestiaSizes.primaryButtonHeight,
      child: IconButton.filledTonal(
        onPressed: onTap,
        iconSize: 32,
        icon: Icon(icon),
      ),
    );
  }
}

class _BottomBar extends StatelessWidget {
  const _BottomBar({
    required this.backLabel,
    required this.onBack,
    required this.nextLabel,
    required this.onNext,
    required this.busy,
  });

  final String backLabel;
  final VoidCallback? onBack;
  final String nextLabel;
  final VoidCallback? onNext;
  final bool busy;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return DecoratedBox(
      decoration: BoxDecoration(
        color: scheme.surface,
        border: Border(top: BorderSide(color: scheme.outlineVariant)),
      ),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(24, 12, 24, 16),
        child: Row(
          children: [
            Expanded(
              child: OutlinedButton(onPressed: onBack, child: Text(backLabel)),
            ),
            const SizedBox(width: 16),
            Expanded(
              flex: 2,
              child: FilledButton(
                onPressed: busy ? null : onNext,
                child: busy
                    ? const SizedBox.square(
                        dimension: 28,
                        child: CircularProgressIndicator(strokeWidth: 3),
                      )
                    : Text(nextLabel),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
