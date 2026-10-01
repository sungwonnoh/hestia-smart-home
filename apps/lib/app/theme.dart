import 'package:flutter/material.dart';

/// 상태 표현용 고정 색. 안전/경고는 일반 알림과 시각적으로 구분한다.
abstract final class HestiaColors {
  static const brand = Color(0xFFD9692E);

  static const safety = Color(0xFFC62828);
  static const safetyContainer = Color(0xFFFDE7E7);
  static const warning = Color(0xFFB45309);
  static const warningContainer = Color(0xFFFFF1DC);
  static const ok = Color(0xFF2E7D32);
  static const okContainer = Color(0xFFE6F4E7);
  static const muted = Color(0xFF8A8580);
}

/// RPi5 터치스크린 기준 크기.
abstract final class HestiaSizes {
  /// 최소 터치 영역 48dp보다 넉넉하게 잡는다.
  static const minTouch = 56.0;
  static const primaryButtonHeight = 64.0;
  static const radius = 20.0;
  static const pagePadding = EdgeInsets.symmetric(horizontal: 24, vertical: 16);
}

abstract final class HestiaTheme {
  static ThemeData light() {
    final scheme = ColorScheme.fromSeed(
      seedColor: HestiaColors.brand,
      brightness: Brightness.light,
    );
    final base = ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      materialTapTargetSize: MaterialTapTargetSize.padded,
      visualDensity: VisualDensity.standard,
    );
    final text = base.textTheme.apply(
      bodyColor: scheme.onSurface,
      displayColor: scheme.onSurface,
    );

    return base.copyWith(
      scaffoldBackgroundColor: scheme.surface,
      textTheme: text.copyWith(
        bodyLarge: text.bodyLarge?.copyWith(fontSize: 18),
        bodyMedium: text.bodyMedium?.copyWith(fontSize: 16),
        titleLarge: text.titleLarge?.copyWith(fontWeight: FontWeight.w700),
        titleMedium: text.titleMedium?.copyWith(
          fontSize: 18,
          fontWeight: FontWeight.w600,
        ),
      ),
      appBarTheme: AppBarTheme(
        backgroundColor: scheme.surface,
        foregroundColor: scheme.onSurface,
        centerTitle: false,
        toolbarHeight: 72,
        titleTextStyle: text.titleLarge?.copyWith(fontWeight: FontWeight.w700),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          minimumSize: const Size(120, HestiaSizes.primaryButtonHeight),
          textStyle: const TextStyle(fontSize: 20, fontWeight: FontWeight.w700),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(HestiaSizes.radius),
          ),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          minimumSize: const Size(120, HestiaSizes.primaryButtonHeight),
          textStyle: const TextStyle(fontSize: 20, fontWeight: FontWeight.w600),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(HestiaSizes.radius),
          ),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          minimumSize: const Size(64, HestiaSizes.minTouch),
          textStyle: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600),
        ),
      ),
      listTileTheme: const ListTileThemeData(
        minVerticalPadding: 14,
        contentPadding: EdgeInsets.symmetric(horizontal: 20),
        titleTextStyle: TextStyle(fontSize: 18, fontWeight: FontWeight.w600),
      ),
      navigationBarTheme: NavigationBarThemeData(
        height: 80,
        indicatorColor: scheme.primaryContainer,
        labelTextStyle: const WidgetStatePropertyAll(
          TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
        ),
      ),
      switchTheme: SwitchThemeData(
        materialTapTargetSize: MaterialTapTargetSize.padded,
        trackOutlineColor: WidgetStatePropertyAll(scheme.outlineVariant),
      ),
      // 터치스크린에서 스크롤 가능 영역을 분명히 보여준다.
      scrollbarTheme: const ScrollbarThemeData(
        thumbVisibility: WidgetStatePropertyAll(true),
        thickness: WidgetStatePropertyAll(6.0),
        radius: Radius.circular(3),
      ),
    );
  }
}
