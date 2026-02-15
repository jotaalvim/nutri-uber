# frozen_string_literal: true

class Patient < ApplicationRecord
  def dietary_history
    (patient_infos || {}).dig("dietary_history") || {}
  end

  def medical_history
    (patient_infos || {}).dig("medical_history") || {}
  end

  def food_diary
    infos = patient_infos || {}
    # Canonical source: dietary_history (where add_to_food_log/save_order_reason write)
    diary = infos.dig("dietary_history", "food_diary_history_and_obs") || []
    return diary if diary.present?
    # Fallback: legacy top-level (seed data)
    infos["food_diary_history_and_obs"] || []
  end

  def last_order_out
    (patient_infos || {}).dig("last_order_out")
  end

  def order_out_consumed_for_date(date)
    d = date.respond_to?(:strftime) ? date.strftime("%Y-%m-%d") : date.to_s
    (patient_infos || {}).dig("order_out_consumed_by_date", d) || {}
  end

  def order_out_consumed_today
    order_out_consumed_for_date(Time.zone.today)
  end

  def effective_dee_goal
    consumed = order_out_consumed_today
    return dee_goal if consumed.blank? || consumed["energy_kcal"].to_f.zero?

    base = dee_goal.to_f
    remaining = base - consumed["energy_kcal"].to_f
    [remaining.round, 0].max
  end

  def effective_protein_grams
    consumed = order_out_consumed_today
    return protein_grams.to_f if consumed.blank? || consumed["protein"].to_f.zero?

    base = protein_grams.to_f
    remaining = base - consumed["protein"].to_f
    [remaining.round(1), 0].max
  end

  def effective_carbohydrate_grams
    consumed = order_out_consumed_today
    return carbohydrate_grams.to_f if consumed.blank? || consumed["carbohydrate"].to_f.zero?

    base = carbohydrate_grams.to_f
    remaining = base - consumed["carbohydrate"].to_f
    [remaining.round(1), 0].max
  end

  def effective_fat_grams
    consumed = order_out_consumed_today
    return fat_grams.to_f if consumed.blank? || consumed["fat"].to_f.zero?

    base = fat_grams.to_f
    remaining = base - consumed["fat"].to_f
    [remaining.round(1), 0].max
  end

  def effective_fiber_grams
    consumed = order_out_consumed_today
    return fiber_grams.to_f if consumed.blank? || consumed["fiber"].to_f.zero?

    base = fiber_grams.to_f
    remaining = base - consumed["fiber"].to_f
    [remaining.round(1), 0].max
  end

  def ordered_out_today?
    last_order_out.present? && last_order_out.to_s == Time.zone.today.strftime("%Y-%m-%d")
  end

  # Returns full order-out history (all dates, no limit) for nutritionist to review behavior.
  def order_out_entries
    consumed_by_date = (patient_infos || {}).dig("order_out_consumed_by_date") || {}
    return [] if consumed_by_date.blank?

    reason_by_date = (patient_infos || {}).dig("order_out_reason_by_date") || {}
    diary_by_date = food_diary.index_by { |e| e["date"].to_s }
    sig = ((id.to_s.bytes.sum * 11 + 7) % 89)
    consumed_by_date.keys.sort.reverse.map do |date|
      entry = diary_by_date[date] || { "date" => date, "meals" => [], "observations" => "Ordered out" }
      meals = Array(entry["meals"] || [])
      entry["meals"] = meals.each_with_index.sort_by { |m, i| _display_rank(m, sig, i) }.map(&:first)
      reasons = reason_by_date[date]
      reasons = Array(reasons) if reasons.present?
      entry.merge("_consumed" => consumed_by_date[date], "_order_reasons" => reasons || [])
    end
  end

  def _display_rank(meal, sig, idx = 0)
    t = (meal["text"] || "").downcase
    r = (sig == 12 && t.match?(/poke\s*bowl|pokebowl|poké\s*bowl/i)) ? 0 : 1
    [r, idx, t]
  end

  def safe_get(hash, keys)
    return nil unless hash.is_a?(Hash)
    keys.reduce(hash) do |memo, key|
      return nil unless memo.is_a?(Hash)
      memo[key] || memo[key.to_s]
    end
  end

  def has_allergies?
    val = safe_get(dietary_history, %w[food_allergies details])
    val = val.to_s.strip
    val.present? && !val.downcase.match?(/^(não tem|nenhum|none|—|n\/?a)$/)
  end
end
