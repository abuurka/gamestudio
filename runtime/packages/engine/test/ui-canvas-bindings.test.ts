import { describe, expect, it } from 'vitest';
import { formatValue, hasBindings, parseBody, renderBody } from '../src/ui/canvas/bindings';

describe('Game UI text bindings', () => {
  it('splits bodies into literals and bindings, with {{ }} as braces', () => {
    expect(parseBody('Score {score}')).toEqual(['Score ', { path: 'score', format: '' }]);
    expect(parseBody('{time:m:ss.00} left')).toEqual([{ path: 'time', format: 'm:ss.00' }, ' left']);
    expect(parseBody('{{literal}} {a.b}')).toEqual(['{literal} ', { path: 'a.b', format: '' }]);
    expect(parseBody('no bindings')).toEqual(['no bindings']);
    expect(parseBody('')).toEqual(['']);
    // Unclosed and empty braces stay literal.
    expect(parseBody('open { brace')).toEqual(['open { brace']);
    expect(parseBody('{}')).toEqual(['{}']);
    expect(hasBindings(parseBody('Hi {name}'))).toBe(true);
    expect(hasBindings(parseBody('Hi {{name}}'))).toBe(false);
  });

  it('renders values through the lookup, missing values as empty text', () => {
    const store: Record<string, unknown> = { score: 7, name: 'Ada', 'save.best': 12.5 };
    const value = (path: string) => store[path];
    expect(renderBody(parseBody('Score {score:000}'), value)).toBe('Score 007');
    expect(renderBody(parseBody('{name} / {save.best:0.0} / {missing}!'), value)).toBe('Ada / 12.5 / !');
    expect(renderBody(parseBody('{{{score}}}'), value)).toBe('{7}');
  });

  it('formats numbers, padding and decimals', () => {
    expect(formatValue(7, '000')).toBe('007');
    expect(formatValue(1234, '000')).toBe('1234');
    expect(formatValue(6.6, '000')).toBe('007');
    expect(formatValue(-5, '000')).toBe('-005');
    expect(formatValue(3.14159, '0.0')).toBe('3.1');
    expect(formatValue(3.14159, '0.00')).toBe('3.14');
    expect(formatValue(2.5, '00.00')).toBe('02.50');
    expect(formatValue('42', '0000')).toBe('0042');
    expect(formatValue(0.1 + 0.2, '')).toBe('0.3');
    expect(formatValue(undefined, '000')).toBe('');
    expect(formatValue(null, '')).toBe('');
    expect(formatValue('hello', '000')).toBe('hello');
    expect(formatValue(true, '')).toBe('true');
    expect(formatValue(5, 'weird')).toBe('5');
  });

  it('formats clocks from seconds, rounding down', () => {
    expect(formatValue(75, 'm:ss')).toBe('1:15');
    expect(formatValue(59.99, 'm:ss')).toBe('0:59');
    expect(formatValue(605.5, 'mm:ss')).toBe('10:05');
    expect(formatValue(83.456, 'm:ss.00')).toBe('1:23.45');
    expect(formatValue(0.29, 'm:ss.00')).toBe('0:00.29');
    expect(formatValue(3725, 'h:mm:ss')).toBe('1:02:05');
    expect(formatValue(-5, 'm:ss')).toBe('-0:05');
  });
});
