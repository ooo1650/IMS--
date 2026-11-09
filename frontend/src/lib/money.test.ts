import { describe, expect, it } from 'vitest'
import { moneyProblem, parseWhole, percentProblem, toBasisPoints, toPaisa, wholeProblem } from './money'

describe('toPaisa: how people type amounts', () => {
  it.each([
    ['120', 12000], ['120.5', 12050], ['120.50', 12050], ['.5', 50], ['1,20,000', 12000000],
    ['Rs 500', 50000], ['Rs. 500', 50000], ['NPR 500', 50000], ['रु 500', 50000], ['500/-', 50000],
    ['५००', 50000], ['१२०.५', 12050], [' 99 ', 9900],
  ])('%s -> %i', (input, paisa) => expect(toPaisa(input)).toBe(paisa))

  it.each(['', '.', 'abc', '-5', '1.234', '1e3', '12abc', '1000000000000'])('refuses %s', (input) =>
    expect(toPaisa(input)).toBeNull())
})

describe('moneyProblem: says why', () => {
  it('is quiet for good or empty input', () => {
    expect(moneyProblem('')).toBeNull()
    expect(moneyProblem('Rs 1,200.50')).toBeNull()
  })
  it('explains the common mistakes', () => {
    expect(moneyProblem('-5')).toBe("Can't be negative")
    expect(moneyProblem('1.234')).toBe('At most 2 decimal places (paisa)')
    expect(moneyProblem('twelve')).toMatch(/^Enter an amount/)
    expect(moneyProblem('1000000000000')).toBe('That amount is too large')
  })
})

describe('percentages', () => {
  it('accepts a trailing % and Nepali digits', () => {
    expect(toBasisPoints('10%')).toBe(1000)
    expect(toBasisPoints('१२.५')).toBe(1250)
  })
  it('refuses over 100% with a reason', () => {
    expect(toBasisPoints('150')).toBeNull()
    expect(percentProblem('150')).toBe("Can't be more than 100%")
  })
})

describe('whole numbers', () => {
  it('parses what people type', () => {
    expect(parseWhole('१२')).toBe(12)
    expect(parseWhole(' 7 ')).toBe(7)
    expect(parseWhole('-3')).toBe(-3)
    expect(parseWhole('1.5')).toBeNull()
  })
  it('explains', () => {
    expect(wholeProblem('')).toBe('Enter a number')
    expect(wholeProblem('1.5')).toBe('Whole numbers only (no decimals)')
    expect(wholeProblem('-2')).toBe("Can't be negative")
    expect(wholeProblem('0', { min: 1 })).toBe('At least 1')
    expect(wholeProblem('5', { max: 3 })).toBe('At most 3')
    expect(wholeProblem('12')).toBeNull()
  })
})
