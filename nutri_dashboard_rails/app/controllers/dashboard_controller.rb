# frozen_string_literal: true

require "net/http"
require "json"

class DashboardController < ApplicationController
  skip_before_action :verify_authenticity_token, only: [:nutrition, :add_to_food_log]

  FOOD_FINDER_URL = ENV.fetch("FOOD_FINDER_URL", "http://127.0.0.1:5001")

  def index
    @patients = Patient.order(:patient_name)
    @selected_patient = @patients.first
    if params[:patient_id].present?
      @selected_patient = Patient.find_by(id: params[:patient_id]) || @selected_patient
    end
    @initial_food_items = fetch_initial_food_items(@selected_patient)
  end

  def fetch_initial_food_items(patient)
    return [] unless patient

    food = fetch_from_api("/cached_food?patient_id=#{patient.id}&city=braga-norte")
    basket = fetch_from_api("/cached_grocery_basket?patient_id=#{patient.id}&city=braga-norte")
    return [] if food.nil? && basket.nil?

    restaurant_items = (food&.dig("items") || []).map { |i| i.merge("from_cache" => food["from_cache"]) }
    basket_items = (basket&.dig("items") || []).map do |i|
      i.merge(
        "restaurant" => basket["store"] || i["restaurant"],
        "restaurant_url" => basket["store_url"] || i["restaurant_url"],
        "store_url" => basket["store_url"]
      )
    end
    (restaurant_items + basket_items).shuffle.take(20)
  end

  def fetch_from_api(path)
    uri = URI("#{FOOD_FINDER_URL}#{path}")
    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = uri.scheme == "https"
    http.open_timeout = 3
    http.read_timeout = 5
    response = http.get(uri)
    return nil unless response.is_a?(Net::HTTPSuccess)

    JSON.parse(response.body)
  rescue Errno::ECONNREFUSED, SocketError, JSON::ParserError
    nil
  end

  def cached_grocery_basket
    patient = Patient.find_by(id: params[:patient_id])
    return head :not_found unless patient

    uri = URI("#{FOOD_FINDER_URL}/cached_grocery_basket?patient_id=#{patient.id}&city=#{params[:city].presence || 'braga-norte'}")
    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = uri.scheme == "https"
    http.open_timeout = 3
    http.read_timeout = 5

    response = http.get(uri)
    body = JSON.parse(response.body)

    if response.is_a?(Net::HTTPSuccess)
      render json: body
    else
      render json: body, status: response.code.to_i
    end
  rescue Errno::ECONNREFUSED, SocketError
    head :service_unavailable
  rescue StandardError
    head :internal_server_error
  end

  def cached_food
    patient = Patient.find_by(id: params[:patient_id])
    return head :not_found unless patient

    uri = URI("#{FOOD_FINDER_URL}/cached_food?patient_id=#{patient.id}&city=#{params[:city].presence || 'braga-norte'}")
    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = uri.scheme == "https"
    http.open_timeout = 3
    http.read_timeout = 5

    response = http.get(uri)
    body = JSON.parse(response.body)

    if response.is_a?(Net::HTTPSuccess)
      render json: body
    else
      render json: body, status: response.code.to_i
    end
  rescue Errno::ECONNREFUSED, SocketError
    head :service_unavailable
  rescue StandardError
    head :internal_server_error
  end

  def warm_cache
    patient = Patient.find_by(id: params[:patient_id])
    return head :bad_request unless patient

    uri = URI("#{FOOD_FINDER_URL}/warm_cache")
    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = uri.scheme == "https"
    http.open_timeout = 3
    http.read_timeout = 5

    request = Net::HTTP::Post.new(uri)
    request["Content-Type"] = "application/json"
    request.body = {
      patient_id: patient.id,
      patient: patient_to_api_format(patient),
      city: params[:city].presence || "braga-norte"
    }.to_json

    response = http.request(request)
    render json: JSON.parse(response.body), status: response.code.to_i
  rescue Errno::ECONNREFUSED, SocketError
    render json: { status: "unavailable" }, status: :service_unavailable
  rescue StandardError
    render json: { status: "error" }, status: :internal_server_error
  end

  def grocery_basket
    patient = Patient.find_by(id: params[:patient_id])
    return render json: { error: "Patient not found" }, status: :not_found unless patient

    payload = patient_to_api_format(patient)
    uri = URI("#{FOOD_FINDER_URL}/grocery_basket")
    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = uri.scheme == "https"
    http.open_timeout = 5
    http.read_timeout = 120

    request = Net::HTTP::Post.new(uri)
    request["Content-Type"] = "application/json"
    request.body = {
      patient: payload,
      patient_id: patient.id,
      city: params[:city].presence || "braga-norte"
    }.to_json

    response = http.request(request)
    body = JSON.parse(response.body)

    if response.is_a?(Net::HTTPSuccess)
      render json: body
    else
      render json: { error: body["error"] || "Grocery basket error" }, status: :unprocessable_entity
    end
  rescue Errno::ECONNREFUSED, SocketError
    render json: {
      error: "Food finder API not reachable at #{FOOD_FINDER_URL}. Start it with: python api.py"
    }, status: :service_unavailable
  rescue StandardError => e
    render json: { error: e.message }, status: :internal_server_error
  end

  def add_basket_to_cart
    store_url = params[:store_url]
    items = params[:items] || []
    return render json: { error: "store_url and items required" }, status: :bad_request if store_url.blank? || items.empty?

    uri = URI("#{FOOD_FINDER_URL}/add_basket_to_cart")
    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = uri.scheme == "https"
    http.open_timeout = 5
    http.read_timeout = 180

    request = Net::HTTP::Post.new(uri)
    request["Content-Type"] = "application/json"
    request.body = {
      store_url: store_url,
      items: items.map { |i| { "name" => i["name"] } },
      keep_open: true
    }.to_json

    response = http.request(request)
    body = JSON.parse(response.body)

    if response.is_a?(Net::HTTPSuccess)
      render json: body
    else
      render json: body, status: response.code.to_i
    end
  rescue Errno::ECONNREFUSED, SocketError
    render json: { error: "Food finder API not reachable" }, status: :service_unavailable
  rescue StandardError => e
    render json: { error: e.message }, status: :internal_server_error
  end

  def add_to_food_log
    patient = Patient.find_by(id: params[:patient_id])
    return render json: { error: "Patient not found" }, status: :not_found unless patient

    items = params[:items] || []
    meal_type = params[:meal_type].presence || "Refeição"
    return render json: { error: "No items to add" }, status: :bad_request if items.empty?

    from_order = params[:from_order].to_s == "true" || params[:from_order] == true

    infos = patient.patient_infos || {}
    diary_key = "food_diary_history_and_obs"
    dietary = infos["dietary_history"] || infos.dig("dietary_history") || {}
    diary = dietary[diary_key] || dietary["food_diary_history_and_obs"] || []

    today = Time.zone.today.strftime("%Y-%m-%d")
    meals = items.map do |item|
      {
        "meal_type" => meal_type,
        "text" => [item["name"], item["restaurant"]].compact.join(" @ ")
      }
    end

    obs = from_order ? "Ordered out" : "Ordered via Nutri-Uber basket"
    new_entry = {
      "date" => today,
      "meals" => meals,
      "observations" => obs
    }

    existing_today = diary.find { |e| e["date"] == today }
    if existing_today
      existing_today["meals"] ||= []
      existing_today["meals"].concat(meals)
      existing_today["observations"] = [
        existing_today["observations"],
        obs
      ].compact.join("\n")
    else
      diary << new_entry
    end

    infos["dietary_history"] ||= {}
    infos["dietary_history"][diary_key] = diary
    infos["last_order_out"] = today if from_order

    if from_order
      consumed_by_date = infos["order_out_consumed_by_date"] || {}
      consumed_today = consumed_by_date[today] || { "energy_kcal" => 0, "protein" => 0, "carbohydrate" => 0, "fat" => 0, "fiber" => 0 }
      items.each do |item|
        nut = item["nutriments"] || item["nutrients"] || {}
        energy = (nut["energy_kcal"] || nut["energy"] || 0).to_f
        has_macros = ((nut["protein"] || 0).to_f + (nut["carbohydrate"] || nut["carbs"] || 0).to_f + (nut["fat"] || 0).to_f) > 0
        if energy.zero? && !has_macros
          energy = 500.0
          consumed_today["protein"] += 30.0
          consumed_today["carbohydrate"] += 55.0
          consumed_today["fat"] += 17.0
          consumed_today["fiber"] += 3.0
        else
          consumed_today["protein"] += (nut["protein"] || 0).to_f
          consumed_today["carbohydrate"] += (nut["carbohydrate"] || nut["carbs"] || 0).to_f
          consumed_today["fat"] += (nut["fat"] || 0).to_f
          consumed_today["fiber"] += (nut["fiber"] || 0).to_f
        end
        consumed_today["energy_kcal"] += energy
      end
      consumed_today["energy_kcal"] = consumed_today["energy_kcal"].round
      consumed_today["protein"] = consumed_today["protein"].round(1)
      consumed_today["carbohydrate"] = consumed_today["carbohydrate"].round(1)
      consumed_today["fat"] = consumed_today["fat"].round(1)
      consumed_today["fiber"] = consumed_today["fiber"].round(1)
      consumed_by_date[today] = consumed_today
      infos["order_out_consumed_by_date"] = consumed_by_date
    end

    patient.update!(patient_infos: infos)

    render json: { status: "ok", message: "Added to food log", diary: diary, last_order_out: infos["last_order_out"] }
  rescue StandardError => e
    render json: { error: e.message }, status: :internal_server_error
  end

  def find_food
    patient = Patient.find_by(id: params[:patient_id])
    return render json: { error: "Patient not found" }, status: :not_found unless patient

    payload = patient_to_api_format(patient)
    uri = URI("#{FOOD_FINDER_URL}/find_food")
    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = uri.scheme == "https"
    http.open_timeout = 5
    http.read_timeout = 120

    request = Net::HTTP::Post.new(uri)
    request["Content-Type"] = "application/json"
    request.body = {
      patient: payload,
      patient_id: patient.id,
      city: params[:city].presence || "braga-norte",
      max_restaurants: 3
    }.to_json

    response = http.request(request)
    body = JSON.parse(response.body)

    if response.is_a?(Net::HTTPSuccess)
      render json: body
    else
      render json: { error: body["error"] || "Food finder error" }, status: :unprocessable_entity
    end
  rescue Errno::ECONNREFUSED, SocketError => e
    render json: {
      error: "Food finder API not reachable at #{FOOD_FINDER_URL}. Start it with: python api.py",
      hint: "If Rails runs in Docker, set FOOD_FINDER_URL=http://host.docker.internal:5001"
    }, status: :service_unavailable
  rescue StandardError => e
    render json: { error: e.message }, status: :internal_server_error
  end

  def nutrition
    q = params[:q].to_s.strip
    return render json: { error: "q (query) required" }, status: :bad_request if q.blank?

    description = params[:description].to_s.strip
    image_url = params[:image_url].to_s.strip.presence
    refresh = params[:refresh].to_s == "1" || params[:refresh] == true

    http = Net::HTTP.new(URI(FOOD_FINDER_URL).host, URI(FOOD_FINDER_URL).port)
    http.use_ssl = URI(FOOD_FINDER_URL).scheme == "https"
    http.open_timeout = 3
    http.read_timeout = 15

    if image_url.present? || description.present?
      uri = URI("#{FOOD_FINDER_URL}/nutrition")
      uri.query = "refresh=1" if refresh
      request = Net::HTTP::Post.new(uri)
      request["Content-Type"] = "application/json"
      request.body = { q: q, description: description, image_url: image_url, refresh: refresh }.to_json
      response = http.request(request)
    else
      uri = URI("#{FOOD_FINDER_URL}/nutrition")
      uri.query = URI.encode_www_form(refresh ? { q: q, refresh: "1" } : { q: q })
      response = http.get(uri)
    end

    body = JSON.parse(response.body)

    if response.is_a?(Net::HTTPSuccess)
      render json: body
    else
      render json: body, status: response.code.to_i
    end
  rescue Errno::ECONNREFUSED, SocketError
    render json: { error: "Food finder API not reachable" }, status: :service_unavailable
  rescue StandardError => e
    render json: { error: e.message }, status: :internal_server_error
  end

  private

  def patient_to_api_format(patient)
    {
      patient_name: patient.patient_name,
      dee_goal: patient.dee_goal,
      dee_goal_unit: patient.dee_goal_unit,
      macronutrient_distribution_in_grams: {
        fat: patient.fat_grams,
        carbohydrate: patient.carbohydrate_grams,
        protein: patient.protein_grams
      },
      fiber_quantity_in_grams: patient.fiber_grams,
      patient_infos: patient.patient_infos || {}
    }
  end
end
