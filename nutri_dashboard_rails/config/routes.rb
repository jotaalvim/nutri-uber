Rails.application.routes.draw do
  mount ActionCable.server => "/cable"

  # Define your application routes per the DSL in https://guides.rubyonrails.org/routing.html

  # Reveal health status on /up that returns 200 if the app boots with no exceptions, otherwise 500.
  # Can be used by load balancers and uptime monitors to verify that the app is live.
  get "up" => "rails/health#show", as: :rails_health_check

  # Render dynamic PWA files from app/views/pwa/* (remember to link manifest in application.html.erb)
  # get "manifest" => "rails/pwa#manifest", as: :pwa_manifest
  # get "service-worker" => "rails/pwa#service_worker", as: :pwa_service_worker

  # Defines the root path route ("/")
  root "dashboard#index"
  get "dashboard", to: "dashboard#index", as: :dashboard
  get "find_food", to: "dashboard#find_food", as: :find_food
  get "warm_cache", to: "dashboard#warm_cache", as: :warm_cache
  get "cached_food", to: "dashboard#cached_food", as: :cached_food
  get "cached_grocery_basket", to: "dashboard#cached_grocery_basket", as: :cached_grocery_basket
  get "grocery_basket", to: "dashboard#grocery_basket", as: :grocery_basket
  post "add_to_food_log", to: "dashboard#add_to_food_log", as: :add_to_food_log
  post "save_order_reason", to: "dashboard#save_order_reason", as: :save_order_reason
  post "add_basket_to_cart", to: "dashboard#add_basket_to_cart", as: :add_basket_to_cart
  match "nutrition", to: "dashboard#nutrition", as: :nutrition, via: [:get, :post]
  post "chat", to: "dashboard#chat", as: :chat
  post "check_food_medication", to: "dashboard#check_food_medication", as: :check_food_medication
  post "bowel_impact", to: "dashboard#bowel_impact", as: :bowel_impact
end
