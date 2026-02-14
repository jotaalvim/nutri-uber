# frozen_string_literal: true

return if Patient.any?

data_path = if ENV["RELEASE_ROOT"] || ENV["DOCKER"]
  "/app/seed_data/input_nutri_approval (3).jsonl"
else
  Rails.root.join("..", "data", "input_nutri_approval (3).jsonl").expand_path.to_s
end

patients_data = begin
  JSON.parse(File.read(data_path))
rescue Errno::ENOENT, JSON::ParserError
  []
end

patients_data.each do |pd|
  macros = pd["macronutrient_distribution_in_grams"] || {}
  Patient.create!(
    patient_name: pd["patient_name"],
    dee_goal: pd["dee_goal"],
    dee_goal_unit: pd["dee_goal_unit"],
    fat_grams: macros["fat"],
    carbohydrate_grams: macros["carbohydrate"],
    protein_grams: macros["protein"],
    fiber_grams: pd["fiber_quantity_in_grams"],
    patient_infos: pd["patient_infos"] || {}
  )
end

puts "Seeded #{patients_data.size} patient(s)"
