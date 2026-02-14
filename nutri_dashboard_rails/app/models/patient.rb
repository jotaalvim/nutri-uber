# frozen_string_literal: true

class Patient < ApplicationRecord
  def dietary_history
    (patient_infos || {}).dig("dietary_history") || {}
  end

  def medical_history
    (patient_infos || {}).dig("medical_history") || {}
  end

  def food_diary
    (patient_infos || {}).dig("food_diary_history_and_obs") || []
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
