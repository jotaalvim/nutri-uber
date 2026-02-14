class CreatePatients < ActiveRecord::Migration[8.0]
  def change
    create_table :patients do |t|
      t.string :patient_name
      t.integer :dee_goal
      t.string :dee_goal_unit
      t.float :fat_grams
      t.float :carbohydrate_grams
      t.float :protein_grams
      t.float :fiber_grams
      t.json :patient_infos

      t.timestamps
    end
  end
end
