import 'package:flutter/material.dart';

import '../../app/hestia_scope.dart';
import '../../app/theme.dart';
import '../../core/state/async_controller.dart';
import '../../core/utils/display_labels.dart';
import '../../core/widgets/hestia_card.dart';
import '../../core/widgets/state_views.dart';
import '../../models/explanation.dart';
import '../../repositories/hestia_repository.dart';

class ExplanationResult {
  const ExplanationResult(this.value);
  final Explanation? value;
}

class ExplanationController extends AsyncController<ExplanationResult> {
  ExplanationController(this._repository, this.explanationId);

  final HestiaRepository _repository;

  /// null이면 최신 판단을 보여준다.
  final String? explanationId;

  @override
  Future<ExplanationResult> fetch() async => ExplanationResult(
        explanationId == null
            ? await _repository.getLatestExplanation()
            : await _repository.getExplanation(explanationId!),
      );

  @override
  bool isEmptyData(ExplanationResult data) => data.value == null;
}

/// HESTIA 판단 과정.
///
/// Context Engine이 만든 근거를 보여주기만 한다. 앱에서 새로 판단하지 않는다.
class ExplanationPage extends StatefulWidget {
  const ExplanationPage({super.key, this.explanationId});

  final String? explanationId;

  @override
  State<ExplanationPage> createState() => _ExplanationPageState();
}

class _ExplanationPageState extends State<ExplanationPage> {
  late final ExplanationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = ExplanationController(
      HestiaScope.of(context).repository,
      widget.explanationId,
    )..load();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('HESTIA 판단')),
      body: AsyncStateView<ExplanationResult>(
        controller: _controller,
        emptyIcon: Icons.psychology_alt_rounded,
        emptyTitle: '아직 설명할 판단이 없습니다',
        emptyMessage: 'HESTIA가 상황을 판단하면 근거를 여기에서 보여드립니다.',
        builder: (context, result) => _ExplanationBody(result.value!),
      ),
    );
  }
}

class _ExplanationBody extends StatelessWidget {
  const _ExplanationBody(this.explanation);

  final Explanation explanation;

  @override
  Widget build(BuildContext context) {
    final e = explanation;
    final summary = Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _StateCard(explanation: e),
        if (e.confidence != null) ...[
          const SizedBox(height: 16),
          _ConfidenceCard(confidence: e.confidence!),
        ],
        if (e.action != null) ...[
          const SizedBox(height: 16),
          _ActionCard(action: e.action!),
        ],
      ],
    );
    final factors = _FactorsCard(factors: e.factors);

    return LayoutBuilder(
      builder: (context, constraints) {
        final wide = constraints.maxWidth >= 760;
        return Scrollbar(
          child: SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(24, 8, 24, 24),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                if (wide)
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Expanded(child: summary),
                      const SizedBox(width: 16),
                      Expanded(child: factors),
                    ],
                  )
                else ...[
                  summary,
                  const SizedBox(height: 16),
                  factors,
                ],
                const SizedBox(height: 20),
                Text(
                  '이 판단은 HESTIA Context Engine이 센서·가전 정보를 바탕으로 내렸습니다.',
                  style: Theme.of(context).textTheme.bodyMedium,
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

class _StateCard extends StatelessWidget {
  const _StateCard({required this.explanation});

  final Explanation explanation;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return HestiaCard(
      color: theme.colorScheme.primaryContainer,
      child: Row(
        children: [
          Icon(
            ContextLabels.icon(explanation.contextName, explanation.state),
            size: 48,
            color: theme.colorScheme.primary,
          ),
          const SizedBox(width: 16),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '현재 상태 · ${ContextLabels.name(explanation.contextName)}',
                  style: theme.textTheme.bodyLarge,
                ),
                Text(
                  ContextLabels.state(explanation.state),
                  style: theme.textTheme.headlineMedium?.copyWith(
                    fontWeight: FontWeight.w800,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _ConfidenceCard extends StatelessWidget {
  const _ConfidenceCard({required this.confidence});

  final double confidence;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final percent = (confidence * 100).round();
    return HestiaCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(child: Text('신뢰도', style: theme.textTheme.titleMedium)),
              Text(
                '$percent%',
                style: theme.textTheme.headlineSmall?.copyWith(
                  fontWeight: FontWeight.w800,
                  color: theme.colorScheme.primary,
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          ClipRRect(
            borderRadius: BorderRadius.circular(6),
            child: LinearProgressIndicator(
              value: confidence.clamp(0.0, 1.0),
              minHeight: 12,
            ),
          ),
        ],
      ),
    );
  }
}

class _ActionCard extends StatelessWidget {
  const _ActionCard({required this.action});

  final String action;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return HestiaCard(
      color: HestiaColors.okContainer,
      child: Row(
        children: [
          const Icon(Icons.task_alt_rounded, color: HestiaColors.ok, size: 32),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('결과', style: theme.textTheme.bodyLarge),
                Text(action, style: theme.textTheme.titleMedium),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _FactorsCard extends StatelessWidget {
  const _FactorsCard({required this.factors});

  final List<ExplanationFactor> factors;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return HestiaCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('판단 근거', style: theme.textTheme.titleMedium),
          const SizedBox(height: 8),
          if (factors.isEmpty)
            Text('전달된 근거가 없습니다.', style: theme.textTheme.bodyLarge)
          else
            for (final f in factors)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 8),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(
                      f.satisfied
                          ? Icons.check_circle_rounded
                          : Icons.remove_circle_outline_rounded,
                      color: f.satisfied ? HestiaColors.ok : HestiaColors.muted,
                      size: 28,
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(f.label, style: theme.textTheme.titleMedium),
                          if (f.detail != null)
                            Text(f.detail!, style: theme.textTheme.bodyMedium),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
        ],
      ),
    );
  }
}
