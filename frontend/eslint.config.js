import js from '@eslint/js';
import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  {
    ignores: [
      'dist/**',
      'node_modules/**',
      'android/**',
      'electron/**',
      'scripts/**',
      'public/**',
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['src/**/*.{ts,tsx}'],
    languageOptions: {
      globals: {
        ...globals.browser,
      },
    },
    plugins: {
      'react-hooks': reactHooks,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // 现有代码里存在大量显式 any 与第三方类型缺口，先降级为提示
      '@typescript-eslint/no-explicit-any': 'off',
      '@typescript-eslint/no-unused-vars': [
        'warn',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      // 空 catch 块在语音/网络降级链中是刻意写法
      'no-empty': 'off',
      'no-console': 'off',
      // WebSocket 消息处理的 switch 分支里直接声明变量，保持按消息类型就近分组
      'no-case-declarations': 'off',
      // markdown 清洗用正则刻意包含控制字符与转义字符
      'no-control-regex': 'off',
      'no-useless-escape': 'off',
      // 表情包解析需要按组合字符匹配 emoji
      'no-misleading-character-class': 'off',
    },
  },
);
