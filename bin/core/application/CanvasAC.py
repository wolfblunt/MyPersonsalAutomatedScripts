"""
Canvas Application Layer
"""
import os
import json
import itertools
import traceback
import concurrent.futures
import time
from bin.common import AppConfigurations, AppConstants
from bin.module_constants.UtilsConstants import logger, CommonUtils, error_obj, uuid_mngmnt
from bin.core.persistence import PersistenceAdaptor
from bin.core.application import WorkflowManagerAC
from bin.core.application import RecentProcessAC
from datetime import datetime
from bin.core.application import CommonAC
from bin.core.audit_applications import AuditManagementAC
from bin.core.application import ConfigurationManagementAC
import copy
from bin.core.application import CollaborationManagementAC, RecipeCommentsAC
# Imports for asynchronous call
from _thread import *
from customexceptionslib.CustomExceptions import CreateProjectException, RecipeRefreshException
from bin.common import AppConfigurations, AppConstants
from bin.core.application import CalculationBuilderAC
from bin.core.application.RecipePropagationStatusAC import get_step_update_status
from decimal import Decimal, getcontext
from bin.core.application.UserRolesAC import check_recipe_builder_access
from flask import request
import jsonpatch

__all__ = ("error", "LockType", "start_new_thread", "interrupt_main", "exit", "allocate_lock",
           "get_ident", "stack_size")


canvas_instance_obj = PersistenceAdaptor.get_instance(CommonUtils.format_ac_to_pc(__name__))
recipe_propagation_instance_obj = PersistenceAdaptor.get_instance(CommonUtils.format_ac_to_pc(__name__))
configuration_management_instance_obj = PersistenceAdaptor.get_instance(CommonUtils.format_ac_to_pc("ConfigurationManagementAC"))


def fetch_users():
    """
    This method is for fetching user details
    :return: User ID JSON
    """
    try:
        json_obj = canvas_instance_obj.fetch_users()
        res_js = []
        for item in json_obj:
            record = canvas_instance_obj.get_source_json(item)
            res_js.append({'user_name': "{} {} ({})".format(record['first_name'], record['last_name'],
                                                            record['user_id']), 'id': record['user_id'],
                           "userId": record["user_id"]})
        res_js = sorted(res_js, key=lambda k: k.get('userId', '').lower())
        return res_js
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def fetch_modalities():
    """
    This method fetches all the modalities
    :return:
    """
    try:
        json_obj = canvas_instance_obj.get_list_modalities()
        res_js = []
        for item in json_obj:
            res_js.append({'modalityName': item['modalityName'], 'id': item['id']})
        res_js = sorted(res_js, key=lambda k: k.get('modalityName', '').lower())
        return res_js
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def get_canvas_static_jsons(resource_name):
    """
    This method is for fetching static JSON based on resource name
    :param resource_name: Resource Name for which static JSON has to be fetched
    :return: Static JSON response
    """
    try:
        resource_path = os.path.join(AppConfigurations.root_path, AppConfigurations.resources_canvas,
                                     "{0}.json".format(resource_name))
        with open(resource_path) as json_data:
            resource_config_js = json.load(json_data)
        logger.info("#---------- Configuration JSON for {0} Fetched Successfully ----------#".format(resource_name))
        return resource_config_js
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))

def check_for_auto_template_selection(input_json):
    try:
        message = "A Site Recipe Step Template has not been selected for one or more Steps"
        if "approvedTemplateDetails" not in input_json:
                approved_template_details = list()
                workspace_id = input_json.get("workspaceTemplateId")
                site_list = input_json.get('sites', [])
                try:
                    temp_site_list = list()
                    for site in site_list:
                        site_obj = {"id": site}
                        temp_site_list.append(site_obj)
                    site_list = temp_site_list
                except Exception as ex:
                    logger.error(str(ex))
                input_data = dict()
                input_data["grWorkspaceId"] = workspace_id
                input_data["sites"] = site_list
                step_details_response = list_step_details_for_site_recipe_creation(input_data)
                for each_step_data in step_details_response:
                    template_obj = each_step_data
                    if "template_details" not in each_step_data and each_step_data.get("configured") == True:
                        raise CreateProjectException(message)
                    else:
                        template_obj["selectedTemplate"] = each_step_data.get("template_details",{}).get("templateName")
                        template_obj["templateId"] = each_step_data.get("template_details",{}).get("templateId")
                        approved_template_details.append(template_obj)
                input_json["approvedTemplateDetails"] = approved_template_details
        else:
            for each_step in input_json.get("approvedTemplateDetails"):
                if each_step.get("configured") and not each_step.get("templateId"):
                    raise CreateProjectException(message)
                elif each_step.get("configured") and each_step.get("templateId") == "default":
                    del each_step["templateId"]
                    del each_step["selectedTemplate"]
                    each_step["configured"] = False
        return input_json
    except CreateProjectException as e:
        logger.error(str(e))
        raise CreateProjectException(str(e))
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))

def create_project(input_json):
    """
    This method creates a project
    :param input_json:
    :return:
    """
    try:
        process_name = input_json.get("processName", "").strip()
        material_id = input_json.get("materialID", "")
        material_name = input_json.get("material_name", "")
        material_row_id = input_json.get("materialRowId", "")
        input_json['processName'] = process_name
        workspace_type = input_json.get("workspaceType")
        if workspace_type == "master":
            input_folder_json = {
                "recipeType": "shared",
                "userId": input_json.get("userId", ""),
                "selectedFilePath": "/",
                "folderPath": input_json.get("selectedFilePath", "")
            }
            res = create_folder(input_folder_json, [])
        wrk_template_id = input_json.get("workspaceTemplateId", "")
        if not wrk_template_id:
            wrk_template_id = fetch_latest_workspace_id(input_json.get("recipeTemplateId", ""))
        input_json["workspaceTemplateId"] = wrk_template_id
        
        if canvas_instance_obj.check_recipe_exists(input_json):
            warning_message = str(AppConstants.CanvasConstants.recipe_already_exists).format(process_name, "ps")
            response = error_obj.result_error_template(message=warning_message, error_category="Warning")
            
        elif material_id:
            if canvas_instance_obj.check_material_exists(material_id):
                warning_message = str(AppConstants.CanvasConstants.material_already_associated).format(process_name, "ps")
                response = error_obj.result_error_template(message=warning_message, error_category="Warning")

            else:
                type_ = "add"
                recipe_or_folder_type = "recipe"
                logger.debug("Type => " + type_)
                logger.debug(str(AppConstants.CanvasConstants.recipe_or_folder_type_logger) + str(recipe_or_folder_type))
                response = {"status": "OK", "message": {}}
                if workspace_type in ["site"]:
                    input_json = check_for_auto_template_selection(input_json)
                recipe_id, workspace_id, workflow_template_id = canvas_instance_obj.create_project(input_json)
                workflow_instance_id = WorkflowManagerAC.create_workflow(process_name, recipe_id, workflow_template_id)
                canvas_instance_obj.add_workflow_instance_to_project(recipe_id, workflow_instance_id)
    
                RecentProcessAC.add_recent_process(input_json, workspace_id, recipe_id)
                response["message"]["recipeId"] = recipe_id
                response["message"]["workspaceId"] = workspace_id
                material_to_recipe_relation_obj = {"materialID": material_id, "recipeId": recipe_id,
                                                   "material_name": material_name, "materialRowId": material_row_id}
    
                if input_json.get("recipeType", "").lower() == 'shared' and input_json.get("materialID", "") not in \
                        ["", None]:
                    canvas_instance_obj.add_material_to_recipe_relation(material_to_recipe_relation_obj)
                    if material_row_id:
                        canvas_instance_obj.add_recipe_details_to_materials(material_row_id, recipe_id,
                                                                            input_json, process_name)
                AuditManagementAC.save_audit_entry()

        else:
            type_ = "add"
            recipe_or_folder_type = "recipe"
            logger.debug("Type => " + type_)
            logger.debug(str(AppConstants.CanvasConstants.recipe_or_folder_type_logger) + str(recipe_or_folder_type))
            response = {"status": "OK", "message": {}}
            # if workspace_type in ["site","master"]:
            #     input_json = check_for_auto_template_selection(input_json)
            if workspace_type in ["site"]:
                input_json = check_for_auto_template_selection(input_json)
            recipe_id, workspace_id, workflow_template_id = canvas_instance_obj.create_project(input_json)
            workflow_instance_id = WorkflowManagerAC.create_workflow(process_name, recipe_id, workflow_template_id)
            canvas_instance_obj.add_workflow_instance_to_project(recipe_id, workflow_instance_id)

            RecentProcessAC.add_recent_process(input_json, workspace_id, recipe_id)
            response["message"]["recipeId"] = recipe_id
            response["message"]["workspaceId"] = workspace_id
            material_to_recipe_relation_obj = {"materialID": material_id, "recipeId": recipe_id,
                                               "material_name": material_name, "materialRowId": material_row_id}
            
            if input_json.get("recipeType", "").lower() == 'shared' and input_json.get("materialID", "") not in \
                    ["", None]:
                canvas_instance_obj.add_material_to_recipe_relation(material_to_recipe_relation_obj)
                if material_row_id:
                    canvas_instance_obj.add_recipe_details_to_materials(material_row_id, recipe_id,
                                                                        input_json, process_name)
            AuditManagementAC.save_audit_entry()

            # For Config Object to Recipe Mapping - Product Family
            config_object_to_recipe_json = {}
            config_object_to_recipe_json["objectType"] = "modality"
            config_object_to_recipe_json["configObjectTitle"] = "Product Family"
            config_object_to_recipe_json["configObjectId"] = input_json.get("productFamilyId", "")

            # Read config object name and version from mongo collection
            config_object_id = input_json.get("productFamilyId", "")
            config_object_result = canvas_instance_obj.fetch_modality_versions_record_on_condition(config_object_id)
            config_object_to_recipe_json["configObjectVersionId"] = config_object_result.get("id", "")
            config_object_to_recipe_json["configObjectVersion"] = config_object_result.get("version", "")
            config_object_to_recipe_json["recipeId"] = response.get("message", {}).get("recipeId", "")

            # Read recipe name and recipe version from Recipe Collection
            recipe_id = response.get("message", {}).get("recipeId", "")
            recipe_result = canvas_instance_obj.fetch_recipe_record(recipe_id)
            config_object_to_recipe_json["recipeName"] = recipe_result.get("processName", "")
            config_object_to_recipe_json["recipeVersion"] = recipe_result.get("version_label", "")
            config_object_to_recipe_json["recipeSubmittedBy"] = input_json.get("userId", "")
            config_object_to_recipe_json["recipeModifiedTs"] = recipe_result.get("modified_ts", "")
            config_object_to_recipe_json['canvasPath'] = config_object_to_recipe_json["recipeName"] + "/"
            config_object_to_recipe_json["workspaceId"] = workspace_id
            config_object_to_recipe_json["updatePath"] = {
                "recipe": {
                    "target_collection": "recipe",
                    "position_details": [
                        {
                            "target_key": "productFamilyName",
                            "path": "/productFamilyName"
                        }
                    ]
                }
            }

            config_object_to_recipe = canvas_instance_obj.add_config_object_to_recipe_relation(
                config_object_to_recipe_json)
        return response
    except CreateProjectException as e:
        logger.error(str(e))
        raise CreateProjectException(str(e))
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def check_recipe_exists(recipe_data):
    """
    This method checks if there is any recipe/process exists in the same name
    :param recipe_data: Input JSON for recipe
    :return: True if already an recipe exists with the same name else False
    """
    try:
        process_name = recipe_data["processName"]
        file_path = recipe_data["selectedFilePath"]
        user_id = recipe_data["userId"]
        recipe_type = recipe_data.get("recipeType", "version")
        recipe_js = {"processName": process_name, "selectedFilePath": file_path, "recipeType": recipe_type,
                     "userId": user_id}
        recipe_exists_status = canvas_instance_obj.check_recipe_exists(recipe_js)
        return recipe_exists_status
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def fetch_activities(step):
    """
    This method is for fetching activities for a particular step
    :param step: Step ID
    :return:
    """
    try:
        step_json = get_canvas_static_jsons("canvas_items_steps_to_activities")
        if step == "":
            response = dict()
            response["activityList"] = []
            activity_records = canvas_instance_obj.fetch_all_activity_records()
            for each_record in activity_records:
                activity_component = each_record.get("id", "")
                response["activityList"].append(
                    {
                        "itemName": each_record.get("activityName", ""),
                        "key": each_record["id"],
                        "component_key": '{}'.format(activity_component),
                        "id": each_record["id"]
                    }
                )
        else:
            response = [step_json]
            step_to_activity_record = canvas_instance_obj.fetch_step_to_activity_record(step)
            activity_list = step_to_activity_record.get("activity", [])
            activity_records = canvas_instance_obj.fetch_multiple_activity_records(activity_list)
            for each_record in activity_records:
                activity_component = each_record.get("id", "")
                response.append(
                    {
                        "label": each_record["activityName"],
                        "key": each_record["id"],
                        "component_key": "{}".format(activity_component)
                    }
                )
        return response
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def fetch_parameters():
    """
    This method fetches all the parameters
    :return: JSON containing all the parameter name and id
    """
    try:
        json_obj = canvas_instance_obj.fetch_parameters()
        res_js = {"content": {"params": []}}
        for item in json_obj:
            res_js["content"]["params"].append({'id': item['id'], 'itemName': item["parameterName"]})
        res_js = sorted(res_js["content"]["params"], key=lambda k: k.get('itemName', '').lower())
        return res_js
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def remove_non_step_equipment_classes_and_equipment(step_to_equipment_class_list, activity_equipment_class_list,
                                                    activity_equipment_list):
    """
    This method filters equipment classes and equipment which are not available in step to equipment class relation
    :param step_to_equipment_class_list: Step Equipment Class List
    :param activity_equipment_class_list: Activity Equipment Class list
    :param activity_equipment_list: Activity Equipment List
    :return:
    """
    try:
        # Initialize
        equipment_class_to_sub_class_json = {}
        complete_equipment_class_list = []
        complete_equipment_list = []

        equipment_sub_class_list = fetch_multiple_equipment_sub_classes_list(step_to_equipment_class_list)

        complete_equipment_class_list += step_to_equipment_class_list
        complete_equipment_class_list += equipment_sub_class_list

        complete_equipment_class_list = list(set(complete_equipment_class_list))


        # Filter Equipment Classes from activity template which are not available in Step to Equipment Class
        activity_equipment_class_list[:] = [each_activity_equip_class for each_activity_equip_class in
                                            activity_equipment_class_list if
                                            each_activity_equip_class.get('equipmentClassId') in
                                            complete_equipment_class_list]
        if activity_equipment_list:
            # Fetch Equipment Records
            equipment_records = canvas_instance_obj.fetch_multiple_equipment_records_using_equipment_class(
                complete_equipment_class_list
            )
            # Iterate through each equipment record and append to complete equipment list
            for each_record in equipment_records:
                complete_equipment_list.append(each_record.get("id"))

            # Filter Equipment from activity template which are not available in Step to Equipment Class Relation
            activity_equipment_list[:] = [each_equipment for each_equipment in activity_equipment_list
                                          if each_equipment.get('equipmentId') in complete_equipment_list]

        return activity_equipment_class_list, activity_equipment_list
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def clean_activity_data(activity_data):
    try:
        for each_activity in activity_data.get("templateObj", {}):

            if "materialTemplateBodyData" not in activity_data.get("templateObj", {}).get(each_activity, {}).get("materials", {}).get(
                "materialTemplateTableMetaInfo", {}) or activity_data.get("templateObj", {}).\
                    get(each_activity, {}).get("materials", {}).get("materialTemplateTableMetaInfo", {}).get(
                "materialTemplateBodyData", []) is None:
                activity_data.get("templateObj", {}).get(
                    each_activity, {}).pop("materials", None)

            if "materialTemplateBodyData" not in activity_data.get("templateObj", {}).get(
                    each_activity, {}).get("srMaterials", {}).get(
                "materialTemplateTableMetaInfo", {}) or activity_data.get("templateObj", {}).get(
                    each_activity, {}).get("srMaterials", {}).get("materialTemplateTableMetaInfo", {}).get(
                    "materialTemplateBodyData", []) is None:
                activity_data.get("templateObj", {}).get(
                    each_activity, {}).pop("srMaterials", None)
        return activity_data

    except Exception as e:
        print(traceback.format_exc())
        logger.error(str(e))
        
        
def fetch_activity_data(activity, step, template_type, page_no=1, page_size=5):
    """
    This method is for fetching parameters for a particular activity
    :param activity: Activity ID
    :param step: Step ID
    :param template_type: template type site/general
    :return: Parameters linked with a particular activity
    """
    try:
        response = {}
        starting_index, ending_index = get_slice_list(int(page_no), int(page_size))
        # Fetch activity template record
        activity_template_record = canvas_instance_obj.fetch_paginated_activity_template_record(activity, starting_index, ending_index)
        if template_type == "experimental":
            activity_parameters = fetch_activity_level_parameters(activity, page_no, page_size, "experimental",
                                                    all_data=False)
        else:
            activity_parameters = fetch_activity_level_parameters(activity, page_no, page_size, "all",
                                                    all_data=False)
        if "templateObj" not in activity_template_record:
            activity_template_record = \
                {"templateObj": {activity: {"params": activity_parameters.get("templateObj", {})
                    .get(activity, {}).get("params", [])}}}
        else:
            activity_template_record["templateObj"][activity]["params"] = activity_parameters.get("templateObj", {})\
                .get(activity, {}).get("params", [])
        response['callback_uri'] = {}
        parameter_uri = "activityParameters"
        response['callback_uri']["parameter_callback"] = activity_parameters.get("parameter_callback", {})
        response['callback_uri']["sr_parameter_callback"] = activity_parameters.get("sr_parameter_callback", {})
        response['callback_uri']["eq_class_parameter_callback"] =\
            "/activityEqClassParameters?&activityId={}".format(activity)
        response['callback_uri']["eq_parameter_callback"] = \
            "/activityEqParameters?&activityId={}".format(activity)
        material_uri = "activityMaterials"
        response['callback_uri']["material_callback"] = \
            fetch_activity_callback_details(material_uri, activity, page_no, page_size, activity_template_record.get("materialCount", 0))
        sr_material_uri = "activitySrMaterials"
        response['callback_uri']["sr_material_callback"] = \
            fetch_activity_callback_details(sr_material_uri, activity, page_no, page_size, activity_template_record.get("srMaterialCount", 0))
        if activity_template_record:
            activity_template_record = clean_activity_data(activity_template_record)
            template_obj = activity_template_record.get("templateObj", {}).get(activity, {})
            if template_type == "general":
                # filter parameters
                template_obj['params'] = [param for param in template_obj.get('params', []) if
                                             param.get('paramType', "general") == "general"]
                template_obj['equipParams'] = [eq_class for eq_class in
                                               template_obj.get('equipParams', []) if
                                                  eq_class.get('eqClassType') == "general"]
                template_obj['equipmentParameters'] = []
                template_obj.pop('srMaterials', {})

            if template_type == "experimental":
                template_obj['materials'] = template_obj.get("srMaterials", {})
                for each_param in template_obj.get("params", []):
                    each_param["paramType"] = "experimental"
                for each_param in template_obj.get("equipParams", []):
                    each_param["eqClassType"] = "experimental"

            # Fetch step to equipment class record
            step_to_equipment_class_record = canvas_instance_obj.fetch_step_to_equipment_class_record(step)

            # Form equipment class list
            step_to_equipment_class_list = step_to_equipment_class_record.get("equipment_class", [])

            # Fetch Activity Equipment Class List
            activity_equipment_class_list = template_obj.get("equipParams", [])

            # Fetch Activity Equipment
            activity_equipment_list = template_obj.get("equipmentParameters", [])

            # Update Activity Equipment Class and Equipment
            remove_non_step_equipment_classes_and_equipment(step_to_equipment_class_list,
                                                            activity_equipment_class_list,
                                                            activity_equipment_list)
            response["templateObj"] = activity_template_record.get("templateObj", {})
            return response

        response = {}
        activity_record = {"params": [], "materials": {}, "equipmentParameters": [], "equipParams": [],
                           "non_editable": False, "activityId": activity}


        activity_record["data"] = []
        response["templateObj"] = {activity : activity_record}
        response['callback_uri'] = {}
        parameter_uri = "activityParameters"
        response['callback_uri']["parameter_callback"] = activity_parameters.get("parameter_callback", {})
        response['callback_uri']["sr_parameter_callback"] = activity_parameters.get("sr_parameter_callback", {})
        response['callback_uri']["eq_class_parameter_callback"] =\
            "/activityEqClassParameters?&activityId={}".format(activity)
        response['callback_uri']["eq_parameter_callback"] = \
            "/activityEqParameters?&activityId={}".format(activity)
        material_uri = "activityMaterials"
        response['callback_uri']["material_callback"] = \
            fetch_activity_callback_details(material_uri, activity, page_no, page_size, activity_template_record.get("materialCount", 0))
        sr_material_uri = "activitySrMaterials"
        response['callback_uri']["sr_material_callback"] = \
            fetch_activity_callback_details(sr_material_uri, activity, page_no, page_size, activity_template_record.get("srMaterialCount", 0))
        return response
    except Exception as e:
        print((traceback.format_exc()))
        logger.error(str(e))
        raise Exception(str(e))


def fetch_activity_parameters(activity, step, template_type):
    """
    This method is for fetching parameters for a particular activity
    :param activity: Activity ID
    :param step: Step ID
    :param template_type: template type site/general
    :return: Parameters linked with a particular activity
    """
    try:
        # Fetch activity template record
        activity_template_record = canvas_instance_obj.fetch_activity_template_record(activity)
        if activity_template_record:
            template_obj = activity_template_record.get("templateObj", {}).get(activity, {})
            if template_type == "general":
                # filter parameters
                template_obj['params'] = [param for param in template_obj.get('params', []) if
                                             param.get('paramType', "general") == "general"]
                template_obj['equipParams'] = [eq_class for eq_class in
                                               template_obj.get('equipParams', []) if
                                                  eq_class.get('eqClassType') == "general"]
                template_obj['equipmentParameters'] = []
                template_obj.pop('srMaterials', {})

            if template_type == "experimental":
                template_obj['materials'] = template_obj.get("srMaterials", {})
                for each_param in template_obj.get("params", []):
                    each_param["paramType"] = "experimental"
                for each_param in template_obj.get("equipParams", []):
                    each_param["eqClassType"] = "experimental"

            # Fetch step to equipment class record
            step_to_equipment_class_record = canvas_instance_obj.fetch_step_to_equipment_class_record(step)

            # Form equipment class list
            step_to_equipment_class_list = step_to_equipment_class_record.get("equipment_class", [])

            # Fetch Activity Equipment Class List
            activity_equipment_class_list = template_obj.get("equipParams", [])

            # Fetch Activity Equipment
            activity_equipment_list = template_obj.get("equipmentParameters", [])

            # Update Activity Equipment Class and Equipment
            remove_non_step_equipment_classes_and_equipment(step_to_equipment_class_list,
                                                            activity_equipment_class_list,
                                                            activity_equipment_list)
            response = activity_template_record.get("templateObj", {})
            return response

        response = {}
        activity_record = {"params": [], "materials": {}, "equipmentParameters": [], "equipParams": [],
                           "non_editable": False, "activityId": activity}

        # Form equipment class list
        equipment_class_list = add_equipment_class_parameters(step)

        activity_record["data"] = equipment_class_list
        response[activity] = activity_record
        return response
    except Exception as e:
        print((traceback.format_exc()))
        logger.error(str(e))
        raise Exception(str(e))


def add_equipment_class_parameters(step):
    """
    This method fetches all equipment class connected to a step
    :param step: Step ID
    :return: Form a JSON by relating step to equipment classes
    """
    equipment_class_parameter_list = []
    try:
        # Fetch step to equipment class record
        step_to_equipment_class_record = canvas_instance_obj.fetch_step_to_equipment_class_record(step)

        # Form equipment class list
        equipment_class_list = step_to_equipment_class_record.get("equipment_class", [])

        # Fetch equipment class records
        equipment_class_records = canvas_instance_obj.fetch_multiple_equipment_class_records(equipment_class_list)

        # Form response data
        for each_record in equipment_class_records:
            # Changed 'equipment_class_name' to 'equipment_sub_class_name'
            equipment_class_label = each_record["equipment_sub_class_name"]
            equipment_class_key = each_record["id"]
            equipment_class_data = {
                "id": equipment_class_key,
                "itemName": equipment_class_label
            }
            equipment_class_parameter_list.append(equipment_class_data)
    except Exception as e:
        logger.error(str(e))
    return equipment_class_parameter_list


def convert_parameter_label_and_value(parameter_values):
    """
    This method converts parameter label and value to itemName and id
    :param parameter_values: Contains list of parameter values
    :return: Converted parameter label and value keys
    """
    try:
        new_parameter_values = dict()
        for each_field in parameter_values:
            new_parameter_values[each_field] = []
            for each_value in parameter_values[each_field]:
                new_parameter_values[each_field].append(
                    {
                        "itemName": each_value["label"],
                        "id": each_value["value"]
                    }
                )
        return new_parameter_values
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def list_activities_parameters(input_json):
    """
    This method fetches parameters for a particular step
    :param step: Step ID
    :return: Parameters of all activities associated with a step
    """
    try:
        # Fetch step template
        # template_record = canvas_instance_obj.fetch_step_template_record(step)
        #
        # step_record = canvas_instance_obj.fetch_step_record(step)
        #
        # # If Step Template is Available return Step Template
        # if template_record:
        #     template_record["stepName"] = step_record.get("stepName", "")
        #     return template_record.get("templateObj", {})

        # Initialize

        step = input_json.get('stepId')
        recipe_type = input_json.get('type')
        response = dict()
        recipe_decorators = dict()
        is_sr_template = False

        # Fetch Equipment Class parameters
        equipment_class_parameter_list = add_equipment_class_parameters(step)

        # Form Response JSON
        response[step] = {"edited": False, "activities": [{
            "label": "Step Information",
            "key": "step_info",
            "id": "step_info",
            "component_key": "step_info"
        },
            {
                "label": "Sampling",
                "key": "sampling",
                "id": "sampling",
                "component_key": "sampling"
            }
        ], "activityParams": {"step_info": {"params": [], "data": equipment_class_parameter_list,
                                            "equipmentParameters": [],
                                            "equipParams": [],
                                            "activityId": "step_info"},
                              "sampling": {"data": [],
                                           "params": [],
                                           "equipmentParameters": [],
                                           "equipParams": [],
                                           "samplingData": [],
                                           "activityId": "sampling"}
                              }, "equipmentClassParams": []
                          }

        # Form equipment class params in final response
        response[step]["equipmentClassParams"] = equipment_class_parameter_list

        if recipe_type == "general":
            template_record = canvas_instance_obj.fetch_latest_approved_gr_template(step)
            if template_record:
                response = template_record[0].get('templateObj', {})

        elif recipe_type == "site" or recipe_type == "experimental":
            template_id = input_json.get('templateId')
            template_record = canvas_instance_obj.fetch_latest_approved_sr_template(step, template_id)
            template_metadata_record = canvas_instance_obj.fetch_step_template_metadata_record(template_id)

            if template_record:
                response = template_record[0].get('templateObj', {})
                is_sr_template = True

                try:
                    template_version = ""
                    major_version = "{major_version}".format(
                        major_version=template_metadata_record.get("latestVersion", {}).get(
                            "major_version", "")
                    )
                    if major_version:
                        template_version = str(AppConstants.CanvasConstants.major_and_minor_version).format(major_version=major_version,
                            minor_version="0")
                    response[step].update({"templateName": template_metadata_record.get("templateName", ""),
                                           "templateVersion": template_version})
                except Exception as e:
                    logger.error(str(e))

            elif not template_id or not template_record:
                template_record = canvas_instance_obj.fetch_latest_approved_gr_template(step)
                if template_record:
                    response = template_record[0].get('templateObj', {})
                    is_gr_template = True
                    logger.debug("Is Gr Template == >" + str(is_gr_template))
                    try:
                        response[step].update({"templateName": template_metadata_record.get("templateName", ""),
                                               "templateVersion": template_metadata_record.get("latestVersion", {}).get(
                                                   "versionLabel", "")})
                    except Exception as e:
                        logger.error(str(e))
            recipe_decorators = update_recipe_decorator_for_general_parameters(response)
        omitted_activities = ['equipment_class_summary', 'equipment_summary', 'solution_class_summary',
                              "equipments_summary", "sampling"]
        # Fetch step to equipment class record
        step_to_equipment_class_record = canvas_instance_obj.fetch_step_to_equipment_class_record(step)

        # Form equipment class list
        step_to_equipment_class_list = step_to_equipment_class_record.get("equipment_class", [])

        for each_activity in response[step].get("activities", []):
            if recipe_type == "experimental":
                # Fetch parameter list
                parameters_list = response[step].get(
                    "activityParams", {}).get(each_activity.get("id", ""), {}).get("params", [])
                for x in range(len(parameters_list)):
                    parameters_list[x]["paramType"] = recipe_type
                response[step]["activityParams"][each_activity.get("id", "")]["params"] = parameters_list

                # Modify materials details
                if is_sr_template:
                    response[step]["activityParams"][each_activity.get("id", "")]["materials"] = \
                        response[step]["activityParams"][each_activity.get("id", "")].get("srMaterials", {})
                    response[step]["activityParams"][each_activity.get("id", "")]["srMat"] = False
                    if response[step]["activityParams"][each_activity.get("id", "")].get("srSampling", {}):
                        response[step]["activityParams"][each_activity.get("id", "")]["sampling"] = \
                            response[step]["activityParams"][each_activity.get("id", "")].get("srSampling", {})
                        response[step]["activityParams"][each_activity.get("id", "")]["srSample"] = False
                # Modify equipment class details
                for eqp_index in range(len(
                        response[step]["activityParams"][each_activity.get("id", "")].get("equipParams", []))):
                    response[step]["activityParams"][each_activity.get("id", "")]["equipParams"][eqp_index][
                        "eqClassType"] = "experimental"

            # Remove spaces for activity key
            # Check if each activity is in activity template list
            # If activity template is available add it to activity_params
            if each_activity.get('id') not in omitted_activities:
                # if each_activity in activity_template_list:
                activity_details = response[step].get('activityParams', {}).get(each_activity.get('id'))
                # Fetch Equipment Class for activity
                activity_equipment_class_list = activity_details.get("equipParams", [])

                # Fetch Equipment for activity
                activity_equipment_list = activity_details.get("equipmentParameters", [])

                # Update Activity Equipment Class and Equipment
                remove_non_step_equipment_classes_and_equipment(step_to_equipment_class_list,
                                                                activity_equipment_class_list,
                                                                activity_equipment_list)
        # Fetch Step to Activity record
        # step_to_activity_record = canvas_instance_obj.fetch_step_to_activity_record(step)
        #
        # # Form activity list
        # activity_list = step_to_activity_record.get("activity", [])
        #
        # activity_template_records = canvas_instance_obj.fetch_multiple_activity_template_records(activity_list)
        #
        # for each_record in activity_template_records:
        #     activity_template_json.update(each_record.get("templateObj", {}))
        #     activity_template_list.append(each_record.get("activityId", ""))
        #
        # # Fetch activity records
        # activity_records = canvas_instance_obj.fetch_multiple_activity_records(activity_list)
        #
        # # Form activity JSON
        # for each_record in activity_records:
        #     activity_json[each_record["id"]] = each_record["activityName"]
        #
        # # Fetch activity to parameters records
        # activity_to_parameters_records = canvas_instance_obj.fetch_multiple_activity_to_parameter_records(activity_list)
        #
        # # Form activity-group JSON
        # for each_record in activity_to_parameters_records:
        #     activity_group_json[each_record["activity"]] = each_record["group"]
        #
        #     # Form group list
        #     for each_group in each_record.get("group", ""):
        #         group_list.append(each_group)
        #
        # # Remove duplicates
        # group_list = list(set(group_list))
        #
        # # Fetch parameter group records
        # parameter_group_records = canvas_instance_obj.fetch_multiple_parameter_group_records(group_list)
        #
        # # Form parameter group JSON
        # for each_record in parameter_group_records:
        #     parameter_group_json[each_record["id"]] = each_record
        #
        #     # Form parameters list
        #     for each_params in each_record["parameters"]:
        #         parameters_list.append(each_params)
        #
        # # Remove duplicates
        # parameters_list = list(set(parameters_list))
        #
        # # Fetch parameter records
        # parameter_records = canvas_instance_obj.fetch_multiple_parameter_records(parameters_list)
        #
        # # Form parameter JSON
        # for each_record in parameter_records:
        #     parameter_json[each_record["id"]] = each_record
        #     # Form uom list
        #     uom_list.append(each_record.get("uom", ""))
        #     # Form parameter template list
        #     parameter_template_list.append(each_record["parameterTemplate"])
        #
        # # Remove duplicates
        # uom_list = list(set(uom_list))
        # parameter_template_list = list(set(parameter_template_list))
        #
        # # Fetch measure records
        # uom_records = canvas_instance_obj.fetch_multiple_uom_records(uom_list)
        #
        # # Form uom JSON
        # for each_record in uom_records:
        #     uom_json[each_record["id"]] = each_record.get("UoM", "")
        #
        # # Fetch parameter template records
        # parameter_template_records = canvas_instance_obj.fetch_multiple_parameter_template_records(
        #     parameter_template_list)
        #
        # # Form parameter template JSON
        # for each_record in parameter_template_records:
        #     parameter_template_json[each_record["id"]] = each_record
        #
        # # Form final response JSON
        # for each_activity in step_to_activity_record.get("activity", []):
        #     activity_name = activity_json[each_activity]
        #
        #     # Remove spaces for activity key
        #     activity_key = activity_name.replace(" ", "_").lower()
        #     activity_component = each_activity
        #
        #     # Add activity details
        #     response[step]["activities"].append(
        #         {
        #             "label": activity_name,
        #             "key": activity_key,
        #             "id": each_activity,
        #             "component_key": activity_component
        #         })
        #
        #     # Check if each activity is in activity template list
        #     # If activity template is available add it to activity_params
        #     if each_activity in activity_template_list:
        #
        #         # Fetch Equipment Class for activity
        #         activity_equipment_class_list = activity_template_json.get(each_activity, {}).get("equipParams", [])
        #
        #         # Fetch step to equipment class record
        #         step_to_equipment_class_record = canvas_instance_obj.fetch_step_to_equipment_class_record(step)
        #
        #         # Form equipment class list
        #         step_to_equipment_class_list = step_to_equipment_class_record.get("equipment_class", [])
        #
        #         # Fetch Equipment for activity
        #         activity_equipment_list = activity_template_json.get(each_activity, {}).get("equipmentParameters", [])
        #
        #         # Update Activity Equipment Class and Equipment
        #         remove_non_step_equipment_classes_and_equipment(step_to_equipment_class_list,
        #                                                         activity_equipment_class_list,
        #                                                         activity_equipment_list)
        #
        #         response[step]["activityParams"][activity_component] = activity_template_json.get(
        #             each_activity, {})
        #
        #     # If Activity Step Template is not available, perform regular operation
        #     else:
        #
        #         # Form activity parameters details
        #         # Added Equipment Class Parameters Details
        #         response[step]["activityParams"][activity_component] = {
        #             "params": [],
        #             "non_editable": False,
        #             "data": equipment_class_parameter_list,
        #             "activityId": activity_component
        #         }
        #
        #         # Iterate through activity group JSON
        #         for each_parameter_group in activity_group_json.get(each_activity, []):
        #             group_parameters = fetch_group_parameters("general", each_parameter_group)
        #             for parameter in group_parameters:
        #                 response[step]["activityParams"][activity_component]["params"].append(
        #                     {
        #                         "param_label": parameter.get("parameterName"),
        #                         "param_key": parameter.get("parameterName").replace(" ", "_").lower(),
        #                         "id": parameter.get("id"),
        #                         "uom": uom_json.get(parameter.get("uom", ""), ""),
        #                         "fields": parameter.get("fields"),
        #                         "value": parameter.get("value"),
        #                         "selectedValue": field_types,
        #                         "facility_fit_configuration": parameter.get("facility_fit_configuration", {})
        #                     }
        #                 )
        return response, recipe_decorators
    except Exception as e:
        response = dict()
        recipe_decorators = dict()
        logger.error(str(e))

        # Default Response
        response[step] = {"edited": False, "activities": [{"label": "Step Information",
                                                           "key": "step_info",
                                                           "id": "step_info",
                                                           "component_key": "step_info"
                                                           }],
                          "activityParams": {"step_info": {"params": [],
                                                           "data": [],
                                                           "equipmentParameters": {},
                                                           "equipParams": []}}, "equipmentClassParams": []}
        return response, recipe_decorators


def list_parameter_attributes():
    """
    This method is for fetching all parameter attribute
    :return:
    """
    try:
        response = canvas_instance_obj.fetch_all_parameter_attributes()
        for each_record in response:
            each_record["facilityFitType"] = each_record.get("facilityFitType") or "NA"
            if not each_record.get("facilityFitAttribute", False):
                try:
                    each_record.pop("facilityFitPriority", None)
                except Exception as e:
                    logger.error(str(e))
        return response
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))
    

def edit_parameter_attributes(input_json):
    try:
        parameter_data = input_json.get("parameterData", {})
        parameter_template_data = {"type": "add", "data": {"fields": parameter_data.get("fields", []),
                                                           "facility_fit_configuration": parameter_data.get(
                                                               "facility_fit_configuration", {}),
                                                           "selectedValue": parameter_data.get("selectedValue", {}),
                                                           "value": parameter_data.get("value", {}),
                                                           "parentTemplateID": parameter_data.get(
                                                               "parameterTemplateID", ""),
                                                           "templateName": parameter_data.get(
                                                               "parameterTemplateName", "")
                                                           }, "id": "",
                                   "object_type": "parameter_templates",
                                   "submittedBy": "system-macro"}
        parameter_type_response = ConfigurationManagementAC.update_objects(parameter_template_data, system=True)
        response = {"status": "OK", "message": "Successfully Updated the Parameter Attributes!",
                    "parameterTemplateID": parameter_type_response.get("id", "")}
        return response
    except Exception as e:
        print(traceback.format_exc())
        logger.error(str(e))
        raise Exception(str(e))


def get_parameter_attributes_details(input_json):
    """
    This method is for fetching parameter attributes with attributes id list
    :return:
    """
    try:
        response_json = {}
        response = canvas_instance_obj.fetch_multiple_patameter_attributes_records(input_json.get("id", []))
        final_json = copy.deepcopy(input_json.get("parameterData", {}))
        fields, options = parameter_data_transformation(response)
        final_json["fields"] = final_json["fields"] + fields
        final_json["value"] = {**final_json["value"], **options}
        response_json["parameterData"] = copy.deepcopy(final_json)
        final_json["parent_id"] = input_json.get("parameterData", {}).get("parameterTemplateID", "")
        record_id = canvas_instance_obj.insert_system_generated_template(final_json)
        final_json["parameterTemplateID"] = record_id
        final_json["parameterID"] = final_json.pop("id")
        return response_json
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def parameter_data_transformation(response):
    try:
        fields_list = []
        options = {}
        for each_field in response:
            if each_field.get("predefinedOptions", []):
                for each_item in each_field.get("predefinedOptions", []):
                    each_item["itemName"] = each_item.pop("label", "")
                    each_item["id"] = each_item.pop("value")
                options[each_field.get("id","")] = each_field.get("predefinedOptions", [])
            fields = {}
            if each_field.get("fieldType", "") is not "calculated_input":
                fields["fieldName"] = each_field.get("fieldName", "")
                fields["fieldType"] = each_field.get("fieldType", "")
                fields["fieldId"] = each_field.get("id", "")
                if each_field.get("isQualifier", False):
                    fields["isQualifier"] = each_field.get("isQualifier", False)
            fields_list.append(fields)
        return fields_list, options
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def delete_parameter_attribute(input_json):
    """
    This method is for updating parameter attributes usage with parameter data
    :return:
    """
    try:
        response_json = {}
        final_json = input_json.get("parameterData", "")
        response_json["parameterData"] = copy.deepcopy(final_json)
        final_json["parent_id"] = input_json.get("parameterData", {}).get("parameterTemplateID", "")
        record_id = canvas_instance_obj.insert_system_generated_template(final_json)
        response_json["parameterData"]["parameterTemplateID"] = record_id
        return response_json
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def get_parameter_details_temp(parameter):
    """
    This method is for fetching parameter details based on parameter ID
    :param parameter: Parameter ID
    :return: Parameters details based on parameter ID
    """
    try:
        # Initialize
        new_parameter_values = dict()

        # Fetch parameter record
        parameter_record = canvas_instance_obj.fetch_parameter_record(parameter)

        # Fetch parameter values
        # parameter_values = parameter_record.get("predefinedOptions", {})
        #
        # # To change display and value keys to itemName and value for frontend
        # for each_field in parameter_values:
        #     new_parameter_values[each_field] = []
        #     for each_value in parameter_values[each_field]:
        #         new_parameter_values[each_field].append(
        #             {
        #                 "itemName": each_value.get("label", ""),
        #                 "id": each_value.get("value", "")
        #             }
        #         )

        # Fetch parameter template ID
        parameter_template_id = parameter_record.get("parameterTemplate", "")

        # Fetch Unit of Measure ID
        uom_id = parameter_record.get("uom", "")

        # Fetch parameter template record
        parameter_template_record = canvas_instance_obj.fetch_parameter_template_record(parameter_template_id)

        # Fetch Unit of Measure record
        uom_record = canvas_instance_obj.fetch_uom_record(uom_id)

        # Fetch facility fit rules
        facility_fit_rules = parameter_template_record.get("facility_fit_configuration", {})

        # adding calculation formula
        calculation_formula = parameter_record.get("field_formula_list", {})
        for field in parameter_template_record.get("fields"):
            if field['fieldType'] == 'calculated_input':
                field.update(calculation_formula.get(field['fieldId'], {}))
            if field['fieldType'] in ['drop_down', 'drop_down_multiselect', 'radio_box', 'check_box']:
                new_parameter_values[field['fieldId']] = []
                for each_value in field.get('predefinedOptions', {}):
                    new_parameter_values[field['fieldId']].append(
                        {
                            "itemName": each_value.get("label", ""),
                            "id": each_value.get("value", "")
                        }
                    )

        # Form response JSON
        response = {
            "id": parameter_record["id"],
            "parameterName": parameter_record["parameterName"],
            "fields": parameter_template_record.get("fields") or "",
            "uom": uom_record.get("UoM") or "",
            "value": new_parameter_values,
            "facility_fit_configuration": facility_fit_rules,
            "selectedValue": {
                "text": "",
                "drop_down": [

                ],
                "radio_box": "",
                "check_box": {
                }
            }
        }
        return response
    except Exception as e:
        print(traceback.format_exc())
        logger.error(str(e))
        raise Exception(str(e))
    

def get_parameter_details(input_json):
    """
    This method is for fetching multiple parameter details based on Parameter ID's
    :param input_json: JSON containing multiple Parameters list
    :return: Parameters details based on parameter ID
    """
    try:
        # Initialize
        response = {"content": {"paramsList": []}}
        parameter_template_json = dict()
        parameter_template_list = []
        uom_json = dict()
        uom_list = []

        # Fetch Multiple Parameter Records
        parameter_records = canvas_instance_obj.fetch_multiple_parameter_records(input_json.get("parameters", []))
        
        # Iterate through each parameter records and form parameter template and uom list
        for each_record in parameter_records:
            parameter_template_list.append(each_record.get("parameterTemplate", ""))
            uom_list.append(each_record.get("uom", ""))
            
        # Remove Duplicates
        parameter_template_list = list(set(parameter_template_list))
        uom_list = list(set(uom_list))
        
        # Fetch Parameter Template Records
        parameter_template_records = canvas_instance_obj.fetch_multiple_parameter_template_records(
            parameter_template_list)
        
        # Fetch Unit of Measure Records
        uom_records = canvas_instance_obj.fetch_multiple_uom_records(uom_list)

        field_id_list = []
        # Iterate through each parameter template record and form parameter template JSON
        for each_record in parameter_template_records:
            parameter_template_json[each_record["id"]] = each_record
            for each_field in each_record.get('fields', ''):
                field_id_list.append(each_field.get('fieldId', ''))


        parameter_attributes_json = {}
        parameter_attribute_records = \
            canvas_instance_obj.fetch_multiple_parameter_attribute_records(list(set(field_id_list)))
        for each_record in parameter_attribute_records:
            parameter_attributes_json[each_record.get("id", "")] = each_record

        # Iterate through each uom record and form UoM JSON
        for each_record in uom_records:
            uom_json[each_record["id"]] = each_record

        # Iterate through each parameter record
        for each_record in parameter_records:
    
            # Adding calculation formula
            new_parameter_values = dict()
            calculation_formula = each_record.get("field_formula_list", {})

            temp_parameter_template_json = copy.deepcopy(parameter_template_json)
            
            for field in temp_parameter_template_json.get(each_record.get("parameterTemplate", ""), {}).get("fields"):
                field.update(parameter_attributes_json.get(field.get('fieldId', ''), {}))
                if field['fieldType'] == 'calculated_input':
                    field.update(calculation_formula.get(field['fieldId'], {}))
                if field['fieldType'] in ['drop_down', 'drop_down_multiselect', 'radio_box', 'check_box']:
                    new_parameter_values[field['fieldId']] = []
                    for each_value in field.get('predefinedOptions', {}):
                        new_parameter_values[field['fieldId']].append(
                            {
                                "itemName": each_value.get("label", ""),
                                "id": each_value.get("value", "")
                            }
                        )
            
            # To resolve deepcopy issue
            temp_fields = copy.deepcopy(
                temp_parameter_template_json.get(each_record.get("parameterTemplate", ""), {}).get(
                    "fields") or [])
            
            # Add Each Parameter to the response
            response["content"]["paramsList"].append(
                {
                    "id": each_record.get("id", ""),
                    "parameterName": each_record.get("parameterName", ""),
                    "fields": temp_fields,
                    "uom": uom_json.get(each_record.get("uom", ""), {}).get("UoM") or "",
                    "value": new_parameter_values,
                    "facility_fit_configuration":
                        parameter_template_json.get(each_record.get("parameterTemplate", ""), {}).get(
                            "facility_fit_configuration", {}),
                    "selectedValue": {
                        "text": "",
                        "drop_down": [
            
                        ],
                        "radio_box": "",
                        "check_box": {
                        }
                    }
                }
            )
        return response
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def field_parameter_mapping(field_value_list):
    """
    This method adds necessary parameter mapping based on the field_type
    :return:
    """
    try:
        value = {}
        for each_field in field_value_list:

            # Changing values for drop down
            if each_field["fieldType"] == "drop_down":
                value[each_field["fieldName"]] = [
                    {
                        "id": each_field.get("value", ""),
                        "itemName": each_field.get("value", "")
                    }
                ]
                each_field["value"] = [
                    {
                        "id": each_field.get("value", ""),
                        "itemName": each_field.get("value", "")
                    }
                ]

            # Changing values for radio box
            elif each_field["fieldType"] == "radio_box":
                value[each_field["fieldName"]] = [
                    {
                        "id": each_field.get("value", ""),
                        "itemName": each_field.get("value", "")
                    }
                ]

            # Changing values fot multi-select drop downs
            elif each_field["fieldType"] == "drop_down_multiselect":
                values = each_field.get("value", "")
                value[each_field["fieldName"]] = []
                each_field["value"] = []
                for each_value in values:
                    value[each_field["fieldName"]].append(
                        {
                            "id": each_value,
                            "itemName": each_value
                        }
                    )
                    each_field["value"].append(
                        {
                            "id": each_value,
                            "itemName": each_value
                        }
                    )
        return value
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def fetch_multiple_equipment_class_parameters_temp(equipment_class_values):
    """"
    This method is for fetching parameters for a particular equipment_class
    :param equipment_class_values: Equipment Class
    :return: Parameters linked with a particular equipment_class
    """
    try:
        response = dict()
        response["content"] = {"paramsList": []}
        equipment_class_list = []
        parameter_template_list = []
        parameter_list = []
        uom_list = []
        parameter_json = dict()
        parameter_template_json = dict()
        uom_json = dict()

        # Fetch Equipment Class 'id' list
        for each_equipment_class in equipment_class_values:
            equipment_class_list.append(each_equipment_class["id"])
        equipment_class_list = list(set(equipment_class_list))

        # Fetch Equipment Class parameters records based on equipment class id's
        equipment_class_parameters_records = canvas_instance_obj.fetch_multiple_equipment_class_parameter_records(
            equipment_class_list)

        for each_record in equipment_class_parameters_records:
            for each_parameter in each_record.get("mappedParameters", []):
                # Fetch Parameter id list
                parameter_list.append(each_parameter.get("parameterId", ""))
                # Fetch Parameter Template id list
                parameter_template_list.append(each_parameter.get("template", ""))

        # Remove duplicate entries
        parameter_list = list(set(parameter_list))
        parameter_template_list = list(set(parameter_template_list))

        # Fetch parameter records
        # Updated: Changed from parameters to equipment parameter definitions
        parameter_records = canvas_instance_obj.fetch_multiple_equipment_parameter_definitions(parameter_list)

        # Fetch parameter template records
        # Updated: Changed from parameter templates to equipment parameter templates
        parameter_template_records = canvas_instance_obj.fetch_multiple_equipment_parameter_template_records(
            parameter_template_list)

        # Form Parameter JSON
        for each_record in parameter_records:
            parameter_json[each_record["id"]] = each_record
            # Fetch uom id list
            uom_list.append(each_record.get("uom", ""))


        # Form Parameter Template JSON
        for each_record in parameter_template_records:
            parameter_template_json[each_record["id"]] = each_record

        # Remove Duplicate entries
        uom_list = list(set(uom_list))

        # Fetch measure records
        uom_records = canvas_instance_obj.fetch_multiple_uom_records(uom_list)

        # Form Unit of Measure JSON
        for each_record in uom_records:
            uom_json[each_record["id"]] = each_record.get("UoM", "")

        # Iterate through each equipment class parameter records and form response JSON
        for each_record in equipment_class_parameters_records:
            # Mapped Parameters
            mapped_parameters = each_record.get("mappedParameters", [])
            for each_parameter in mapped_parameters:
                values = each_parameter.get("values", [])
                fields = parameter_template_json[each_parameter["template"]]["fields"]

                # Fetch facility fit rules for equipment parameters from equipment parameter template
                try:
                    facility_fit_rules = parameter_template_json[each_parameter["template"]][
                        "facility_fit_configuration"]
                except Exception as e:
                    logger.error(str(e))
                    facility_fit_rules = {}

                # Mapping parameter template fields with equipment class parameter values
                field_value_list = CommonAC.merge_template_equip_class_params(fields, values)

                value = field_parameter_mapping(field_value_list)
                parameter = parameter_json.get(each_parameter.get("parameterId", ""), {})
                uom_id = parameter.get("uom", "")
                param_label = parameter.get("parameterName", "")
                # Remove spaces in parameter key
                param_key = param_label.replace(" ", "_").lower()
                uom = uom_json.get(uom_id, "")

                # Final response
                response["content"]["paramsList"].append(
                    {
                        "param_key": param_key,
                        "param_label": param_label,
                        "fields": field_value_list,
                        "value": value,
                        "id": each_parameter["parameterId"],
                        "uom": uom,
                        "facility_fit_configuration": facility_fit_rules
                    }
                )

        return response
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def fetch_multiple_equipment_class_parameters(equipment_class_values):
    """"
    This method is for fetching parameters for a particular equipment_class
    :param equipment_class_values: Equipment Class
    :return: Parameters linked with a particular equipment_class
    """
    try:
        # Initialize Response
        response = dict()
        response["content"] = {"paramsList": []}

        # Iterate through each equipment class
        for each_equipment_class in equipment_class_values:

            # Initialize
            equipment_parameter_list = []
            equipment_parameter_template_list = []
            equipment_parameter_template_json = dict()
            equipment_parameter_json = dict()
            uom_list = []
            uom_json = dict()

            # Fetch equipment class ID
            equipment_class_id = each_equipment_class.get("id", "")

            # Fetch equipment class parameters
            equipment_class_parameters_dict = ConfigurationManagementAC.get_equipment_class_parameters(
                equipment_class_id)
            
            # Iterate through each parameter
            for parameter, parameter_data in list(equipment_class_parameters_dict.items()):
                # Add Parameter ID to equipment_parameter_list and template ID to equipment_parameter_template_list
                equipment_parameter_list.append(parameter)
                
            # Fetch Equipment Parameter records
            equipment_parameter_records = canvas_instance_obj.fetch_multiple_equipment_parameter_definitions(
                equipment_parameter_list)
            
            # Iterate through each equipment parameter records
            for each_record in equipment_parameter_records:
                # Form Equipment Parameter JSON
                equipment_parameter_json[each_record["id"]] = each_record
                
                equipment_parameter_template_list.append(each_record.get("parameterTemplate", ""))

                # Form UoM list
                uom_list.append(each_record.get("uom", ""))
                
            # Fetch Equipment Parameter Template records
            equipment_parameter_template_records = canvas_instance_obj. \
                fetch_multiple_equipment_parameter_template_records(equipment_parameter_template_list)
            
            # Fetch measure records
            uom_records = canvas_instance_obj.fetch_multiple_uom_records(uom_list)

            # Form Unit of Measure JSON
            for each_record in uom_records:
                uom_json[each_record["id"]] = each_record.get("UoM", "")

            field_id_list = []
            # Form Parameter Template JSON
            for each_record in equipment_parameter_template_records:
                equipment_parameter_template_json[each_record["id"]] = each_record
                for each_field in each_record.get('fields', []):
                    field_id_list.append(each_field.get('fieldId', ''))

            parameter_attributes_json = {}
            parameter_attribute_records = canvas_instance_obj.fetch_multiple_parameter_attribute_records(
                field_id_list)
            for each_record in parameter_attribute_records:
                parameter_attributes_json[each_record.get("id", "")] = each_record

            # Iterate through each parameter
            for parameter, parameter_data in list(equipment_class_parameters_dict.items()):

                # Fetch Values
                values = parameter_data.get("values", [])
                
                # Fetch Fields
                fields = equipment_parameter_template_json.get(equipment_parameter_json.get(parameter, {}).get(
                    "parameterTemplate", ""), {}).get("fields", [])
                
                hidden_fields = equipment_parameter_template_json.get(equipment_parameter_json.get(parameter, {}).get(
                    "parameterTemplate", ""), {}).get("pkm__h_fields", [])

                for each_field in fields:
                    each_field.update(parameter_attributes_json.get(each_field.get('fieldId', ''), {}))

                # calculation formula
                calculation_formula = equipment_parameter_json[parameter].get('field_formula_list', {})

                # Fetch facility fit rules for equipment parameters from equipment parameter template
                try:
                    facility_fit_rules = equipment_parameter_template_json[parameter_data["template"]][
                        "facility_fit_configuration"]
                except Exception as e:
                    logger.error(str(e))
                    facility_fit_rules = {}
                    
                # Mapping parameter template fields with equipment class parameter values
                field_value_list = CommonAC.merge_template_equip_class_params(fields, values, calculation_formula,
                                                                              hidden_fields=hidden_fields,
                                                                              parameter_attributes_json=
                                                                              parameter_attributes_json)

                # Map fields and values for parameters
                value = field_parameter_mapping(field_value_list)

                # Fetch parameter data
                parameter_data = equipment_parameter_json.get(parameter, {})

                # Fetch UoM ID
                uom_id = parameter_data.get("uom", "")

                # Fetch Parameter Name
                param_label = parameter_data.get("parameterName", "")
                # Remove spaces in parameter key
                param_key = param_label.replace(" ", "_").lower()

                # Fetch UoM Name
                uom = uom_json.get(uom_id, "")

                # Final Response
                response["content"]["paramsList"].append(
                    {
                        "param_key": param_key,
                        "param_label": param_label,
                        "fields": field_value_list,
                        "value": value,
                        "id": parameter,
                        "uom": uom,
                        "facility_fit_configuration": facility_fit_rules
                    }
                )
        return response
    except Exception as e:
        print(traceback.format_exc())
        logger.error(str(e))
        raise Exception(str(e))


def fetch_multiple_equipment_parameters_temp(equipment_values):
    """"
    This method is for fetching parameters for a particular equipment
    :param equipment_values: Equipment Class
    :return: Parameters linked with a particular equipment
    """
    try:
        response = dict()
        response["content"] = {"paramsList": []}
        equipments_list = []
        parameter_template_list = []
        parameter_list = []
        uom_list = []
        parameter_json = dict()
        parameter_template_json = dict()
        uom_json = dict()

        # Fetch Equipment 'id' list
        for each_equipment in equipment_values:
            equipments_list.append(each_equipment["id"])
        equipments_list = list(set(equipments_list))

        # Fetch Equipment parameters records based on equipment id's
        equipment_parameters_records = canvas_instance_obj.fetch_multiple_equipment_parameter_records(
            equipments_list)

        for each_record in equipment_parameters_records:
            for each_parameter in each_record.get("mappedParameters", []):
                # Fetch Parameter id list
                parameter_list.append(each_parameter.get("parameterId", ""))
                # Fetch Parameter Template id list
                parameter_template_list.append(each_parameter.get("template", ""))

        # Remove duplicate entries
        parameter_list = list(set(parameter_list))
        parameter_template_list = list(set(parameter_template_list))

        # Fetch parameter records
        # Updated: Changed from parameters to equipment parameter definitions
        parameter_records = canvas_instance_obj.fetch_multiple_equipment_parameter_definitions(parameter_list)

        # Fetch parameter template records
        # Updated: Changed from parameter templates to equipment parameter templates
        parameter_template_records = canvas_instance_obj.fetch_multiple_equipment_parameter_template_records(
            parameter_template_list)

        # Form Parameter JSON
        for each_record in parameter_records:
            parameter_json[each_record["id"]] = each_record
            # Fetch uom id list
            uom_list.append(each_record.get("uom", ""))

        # Form Parameter Template JSON
        for each_record in parameter_template_records:
            parameter_template_json[each_record["id"]] = each_record

        # Remove Duplicate entries
        uom_list = list(set(uom_list))

        # Fetch measure records
        uom_records = canvas_instance_obj.fetch_multiple_uom_records(uom_list)

        # Form Unit of Measure JSON
        for each_record in uom_records:
            uom_json[each_record["id"]] = each_record.get("UoM", "")

        # Iterate through each equipment class parameter records and form response JSON
        for each_record in equipment_parameters_records:
            # Mapped Parameters
            mapped_parameters = each_record.get("mappedParameters", [])
            for each_parameter in mapped_parameters:
                values = each_parameter.get("values", [])
                fields = parameter_template_json[each_parameter["template"]]["fields"]

                # Fetch facility fit rules for equipment parameters from equipment parameter template
                try:
                    facility_fit_rules = parameter_template_json[each_parameter["template"]][
                        "facility_fit_configuration"]
                except Exception as e:
                    logger.error(str(e))
                    facility_fit_rules = {}

                # Mapping parameter template fields with equipment class parameter values
                field_value_list = CommonAC.merge_template_equip_class_params(fields, values)

                value = field_parameter_mapping(field_value_list)
                parameter = parameter_json.get(each_parameter.get("parameterId", ""), {})
                uom_id = parameter.get("uom", "")
                param_label = parameter.get("parameterName", "")
                # Remove spaces in parameter key
                param_key = param_label.replace(" ", "_").lower()
                uom = uom_json.get(uom_id, "")

                # Final response
                response["content"]["paramsList"].append(
                    {
                        "param_key": param_key,
                        "param_label": param_label,
                        "fields": field_value_list,
                        "value": value,
                        "id": each_parameter["parameterId"],
                        "uom": uom,
                        "facility_fit_configuration": facility_fit_rules
                    }
                )

        return response
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def fetch_equipment_class_hierarchy_parameters_list(equipment_class_parameter_json,
                                                    equipment_class_hierarchy_json,
                                                    equipment_class_id,
                                                    equipment_class_parameters_list=None):
    """
    This method fetches equipment classes which are parent classes to the given equipment class
    :param equipment_class_parameter_json: Equipment class parameters JSON
    :param equipment_class_hierarchy_json: Equipment class hierarchy JSON contains the hierarchy of equipment classes
    :param equipment_class_id: Equipment class ID
    :param equipment_class_parameters_list: Equipment Class list contains the equipment class parameters and it parent
    class parameters
    :return: Equipment class and its parent class parameters list
    """
    try:
        # Check if equipment class parameters list is None
        # If None assign it as list
        if equipment_class_parameters_list is None:
            equipment_class_parameters_list = []

        # Perform recursion until the equipment class reaches the base class
        if equipment_class_id != AppConstants.ServiceConstants.equipment_class_base_class_id:

            # Fetch equipment class parameters for the equipment class
            equipment_class_parameters = equipment_class_parameter_json.get(equipment_class_id, [])

            # Iterate through each equipment class parameters
            for each_equipment_class_parameter in equipment_class_parameters:

                # If not available already add it to equipment class parameters list
                if each_equipment_class_parameter not in equipment_class_parameters_list:
                    equipment_class_parameters_list.append(each_equipment_class_parameter)

            # Perform Recursion until base class is reached
            fetch_equipment_class_hierarchy_parameters_list(equipment_class_parameter_json,
                                                            equipment_class_hierarchy_json,
                                                            equipment_class_hierarchy_json[equipment_class_id],
                                                            equipment_class_parameters_list)
        return equipment_class_parameters_list
    except Exception as e:
        print((traceback.format_exc()))
        logger.error(str(e))
        raise Exception(str(e))


def equipment_parameters_handler(equipment_values):
    """
    This method is for fetching equipment parameters which are related to the equipments
    :param equipment_values: Equipments List
    :return: Equipment Parameters
    """
    try:
        # Initialize
        response = dict()
        equipments_list = []
        equipment_class_list = []
        complete_equip_class_list = []
        equipment_parameter_template_list = []
        equipment_parameter_list = []
        uom_list = []
        uom_json = dict()
        equipment_parameter_definition_json = dict()
        equipment_parameter_template_json = dict()
        equipment_class_hierarchy_json = dict()
        equipment_class_parameters_json = dict()
        equipment_parameters_json = dict()
        complete_equip_params_json = dict()

        # Iterate through each equipment and form equipments list
        for each_equipment in equipment_values:
            equipments_list.append(each_equipment.get("id", ""))

        # Fetch equipment records
        equipment_records = canvas_instance_obj.fetch_multiple_equipment_records(equipments_list)

        # Iterate through each equipment record and form equipment class list
        for each_record in equipment_records:
            equipment_class_list.append(each_record.get("equipment_class_id", ""))

        # Fetch equipment class hierarchy JSON and complete equipment class list including parent classes
        equipment_class_hierarchy_json, complete_equip_class_list = fetch_equipment_classes_hierarchy_details(
            equipment_class_list,
            complete_equip_class_list,
            equipment_class_hierarchy_json)

        # Fetch equipment class parameter records
        equipment_class_parameter_records = canvas_instance_obj.fetch_multiple_equipment_class_parameter_records(
            complete_equip_class_list
        )

        # Iterate through each equipment class parameter
        for each_record in equipment_class_parameter_records:

            # Fetch mapped parameters
            mapped_parameters = each_record.get("mappedParameters", [])

            if each_record["equipment"] not in equipment_class_parameters_json:
                equipment_class_parameters_json[each_record["equipment"]] = []

            # Iterate through each parameter in mapped parameters
            for each_parameter_record in mapped_parameters:

                # Form equipment parameter ID list
                equipment_parameter_list.append(each_parameter_record.get("parameterId", ""))

                # Form equipment class parameters JSON
                equipment_class_parameters_json[each_record["equipment"]].append(
                    {each_parameter_record.get("parameterId", ""): each_parameter_record})

        # Fetch Equipment parameters records based on equipment id's
        equipment_parameters_records = canvas_instance_obj.fetch_multiple_equipment_parameter_records(
            equipments_list)

        # Iterate through each equipment parameter record
        for each_record in equipment_parameters_records:

            if each_record["equipment"] not in equipment_parameters_json:
                equipment_parameters_json[each_record["equipment"]] = []

            # Iterate through each parameters
            for each_parameter_record in each_record.get("mappedParameters", []):
                # Form Parameter id list
                equipment_parameter_list.append(each_parameter_record.get("parameterId", ""))
                # Form equipment parameters JSON
                equipment_parameters_json[each_record["equipment"]].append(
                    {each_parameter_record.get("parameterId", ""): each_parameter_record})

        # Remove duplicates
        equipment_parameter_list = list(set(equipment_parameter_list))

        # Fetch Equipment Parameter records
        equipment_parameter_records = canvas_instance_obj.fetch_multiple_equipment_parameter_definitions(
            equipment_parameter_list)

        # Iterate through each equipment parameter records
        for each_record in equipment_parameter_records:
            # Form Equipment Parameter JSON
            equipment_parameter_definition_json[each_record["id"]] = each_record

            equipment_parameter_template_list.append(each_record.get("parameterTemplate", ""))

            # Form UoM list
            uom_list.append(each_record.get("uom", ""))
            
        # Remove duplicates
        uom_list = list(set(uom_list))
        equipment_parameter_template_list = list(set(equipment_parameter_template_list))

        # Fetch measure records
        uom_records = canvas_instance_obj.fetch_multiple_uom_records(uom_list)

        # Fetch Equipment Parameter Template records
        equipment_parameter_template_records = canvas_instance_obj. \
            fetch_multiple_equipment_parameter_template_records(equipment_parameter_template_list)

        # Form Unit of Measure JSON
        for each_record in uom_records:
            uom_json[each_record["id"]] = each_record.get("UoM", "")

        field_id_list = []
        # Form Parameter Template JSON
        for each_record in equipment_parameter_template_records:
            equipment_parameter_template_json[each_record["id"]] = each_record

            for each_field in each_record.get('fields', []):
                field_id_list.append(each_field.get('fieldId', ''))

        parameter_attributes_json = {}
        parameter_attribute_records = canvas_instance_obj.fetch_multiple_parameter_attribute_records(
            field_id_list)
        for each_record in parameter_attribute_records:
            parameter_attributes_json[each_record.get("id", "")] = each_record

        # Iterate through each equipment records
        for each_record in equipment_records:

            # Fetch equipment class ID
            equipment_class_id = each_record.get("equipment_class_id", "")

            # Fetch equipment class parameters
            equipment_class_parameters_list = fetch_equipment_class_hierarchy_parameters_list(
                equipment_class_parameters_json,
                equipment_class_hierarchy_json,
                equipment_class_id
            )

            # Fetch equipment ID
            equipment_id = each_record.get("id", "")

            # Fetch equipment parameters
            equipment_parameters_list = equipment_parameters_json.get(equipment_id, [])

            if equipment_id not in complete_equip_params_json:
                complete_equip_params_json[equipment_id] = {}

            # Iterate through each equipment parameter and add to complete equip params JSON
            for each_parameter in equipment_parameters_list:
                for parameter_id, parameter_data in each_parameter.items():
                    complete_equip_params_json[equipment_id][parameter_id] = parameter_data

            # Iterate through each equipment class parameter and add to complete equip params JSON if not available
            for each_parameter in equipment_class_parameters_list:
                for parameter_id, parameter_data in each_parameter.items():
                    if parameter_id not in complete_equip_params_json[equipment_id]:
                        complete_equip_params_json[equipment_id][parameter_id] = parameter_data

        # Iterate through each equipment in complete equip params JSON
        for each_equipment in complete_equip_params_json:

            # Iterate through
            for parameter_id, equipment_parameter_data in complete_equip_params_json[each_equipment].items():

                # Fetch Values
                values = equipment_parameter_data.get("values", [])

                # Fetch Fields
                fields = equipment_parameter_template_json.get(equipment_parameter_definition_json.get(
                    parameter_id, {}).get("parameterTemplate", ""), {}).get("fields", [])

                hidden_fields = equipment_parameter_template_json.get(equipment_parameter_definition_json.get(
                    parameter_id, {}).get("parameterTemplate", ""), {}).get("pkm__h_fields", [])

                for each_field in fields:
                    each_field.update(parameter_attributes_json.get(each_field.get('fieldId', ''), {}))

                # calculation formula
                calculation_formula = equipment_parameter_definition_json[parameter_id].get('field_formula_list', {})

                # Fetch facility fit rules for equipment parameters from equipment parameter template
                try:
                    facility_fit_rules = equipment_parameter_template_json[equipment_parameter_data["template"]][
                        "facility_fit_configuration"]
                except Exception as e:
                    logger.error(str(e))
                    facility_fit_rules = {}

                # Mapping parameter template fields with equipment class parameter values
                field_value_list = CommonAC.merge_template_equip_class_params(
                    fields, values, calculation_formula,
                    hidden_fields=hidden_fields,
                    parameter_attributes_json=parameter_attributes_json)

                # Map fields and values for parameters
                value = field_parameter_mapping(field_value_list)

                # Fetch parameter data
                parameter_id = equipment_parameter_data.get("parameterId", "")
                equipment_parameter_definition_data = equipment_parameter_definition_json.get(parameter_id, {})

                # Fetch UoM ID
                uom_id = equipment_parameter_definition_data.get("uom", "")

                # Fetch Parameter Name
                param_label = equipment_parameter_definition_data.get("parameterName", "")
                # Remove spaces in parameter key
                param_key = param_label.replace(" ", "_").lower()

                # Fetch UoM Name
                uom = uom_json.get(uom_id, "")

                if each_equipment not in list(response.keys()):
                    response[each_equipment] = []

                # Form Response
                response[each_equipment].append({
                    "param_key": param_key,
                    "param_label": param_label,
                    "fields": field_value_list,
                    "value": value,
                    "id": parameter_id,
                    "uom": uom,
                    "facility_fit_configuration": facility_fit_rules
                })
        return response
    except Exception as e:
        print((traceback.format_exc()))
        logger.error(str(e))
        raise Exception(str(e))


def fetch_multiple_equipment_parameters(equipment_values):
    """
    This method fetches equipment parameters based on the equipments list
    :param equipment_values: Equipments List
    :return: Equipment parameters linked to the equipments
    """
    try:
        # Initialize
        response = dict()
        response["content"] = {"paramsList": []}

        # Fetch equipments and the related parameters
        equipments_and_related_parameters_json = equipment_parameters_handler(equipment_values)

        # Iterate through each equipments(includes equipment classes and parent equipment classes)
        for equipment, equipment_parameter_list in equipments_and_related_parameters_json.items():

            # Iterate through each equipment parameter and form response JSON
            for each_parameter in equipment_parameter_list:
                response["content"]["paramsList"].append(each_parameter)
        return response
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def fetch_equipment_class(step):
    """
    List entire equipment class if step is "" or list equipment classes for a Step ID
    :param step: Step ID or ""
    :return: Equipment class
    """
    try:
        json_obj = canvas_instance_obj.get_list_equipment_class(step)
        res_js = []
        for item in json_obj:
            # Changed 'equipment_class_name' to 'equipment_sub_class_name'
            res_js.append({'equipment_class_name': item['equipment_sub_class_name'], 'id': item["id"]})
        res_js = sorted(res_js, key=lambda k: k.get('equipment_class_name', '').lower())
        return res_js
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def fetch_side_bar(modality):
    """
    This method fetches all the details
    :param modality:
    :return:
    """
    try:
        response = dict()
        steps_list = []
        process_area_list = []
        step_json = dict()
        process_area_json = dict()
        order_record = canvas_instance_obj.fetch_record_by_id(AppConfigurations.steps_to_process_area_order_collection,
                                                              AppConfigurations.steps_to_process_area_order_id)
        # Fetch Side Bar JSON
        side_bar_json = get_canvas_static_jsons("canvas_items_side_bar")

        if modality is None or modality == "" or modality == "null":
            step_records = canvas_instance_obj.fetch_all_step_records()
            for each_record in step_records:
                step_json[each_record["id"]] = each_record
                steps_list.append(each_record["id"])

            # Remove duplicate
            steps_list = list(set(steps_list))

        else:
            # Fetch modality to steps records
            modality_to_steps_records = canvas_instance_obj.fetch_multiple_modality_to_steps_records(modality)

            # Fetch Step list
            for each_record in modality_to_steps_records:
                for each_step in each_record["step"]:
                    steps_list.append(each_step)

            # Remove duplicate
            steps_list = list(set(steps_list))

            # Fetch step records
            step_records = canvas_instance_obj.fetch_multiple_step_records(steps_list)

            # Form step JSON
            for each_record in step_records:
                step_json[each_record["id"]] = each_record

        # Fetch step to process area records
        steps_to_process_area_records = canvas_instance_obj.fetch_multiple_steps_to_process_area_records(steps_list)
        steps_to_process_area_records_mapping = {}
        for each_record in steps_to_process_area_records:
            steps_to_process_area_records_mapping[each_record['processArea']] = each_record
        # Form process area list
        for each_record in steps_to_process_area_records:
            process_area_list.append(each_record.get("processArea", ""))

        # Remove duplicates
        process_area_list = list(set(process_area_list))

        # Form process area JSON
        for each_record in canvas_instance_obj.fetch_multiple_process_area_records(process_area_list):
            process_area_json[each_record["id"]] = each_record["processAreaName"]

        # check for steps to process area order and if order is not present
        if not order_record:
            for each_record in steps_to_process_area_records:
                process_area = each_record.get("processArea", "")
                for each_step in each_record.get("step", []):
                    try:
                        side_bar_json["childSections"].append(
                            {
                                "key": step_json[each_step]["stepName"],
                                "label": step_json[each_step]["stepName"],
                                "path": "/static/unitops/",
                                "imageUrl": step_json[each_step]["imageUrl"],
                                "parent": process_area_json[process_area],
                                "subParent": False,
                                "id": each_step
                            }
                        )
                    except Exception as e:
                        logger.error(str(e))
                side_bar_json["parent_sections"].append(
                    {
                        "key": process_area_json[process_area],
                        "label": process_area_json[process_area],
                        "icon": "fa fa-sliders fa-rotate-90",
                        "show": True,
                        "search": True,
                        "id": process_area
                    }
                )

            # Form Response
            response['status'] = "OK"
            response['message'] = side_bar_json
            return response

        # Iterate through each step to process area records and form parent and child sections
        for process_area_order in order_record['process_area']:
            if process_area_order in list(steps_to_process_area_records_mapping.keys()):
                each_record = steps_to_process_area_records_mapping[process_area_order]
                process_area = each_record.get("processArea", "")
                for each_step in order_record.get('steps')[process_area_order]:
                    if each_step in each_record.get("step", []):
                        try:
                            side_bar_json["childSections"].append(
                                {
                                    "key": step_json[each_step]["stepName"],
                                    "label": step_json[each_step]["stepName"],
                                    "path": "/static/unitops/",
                                    "imageUrl": step_json[each_step]["imageUrl"],
                                    "parent": process_area_json[process_area],
                                    "subParent": False,
                                    "id": each_step
                                }
                            )
                        except Exception as e:
                            logger.debug(str(e))
                            logger.debug(traceback.format_exc())
                side_bar_json["parent_sections"].append(
                    {
                        "key": process_area_json[process_area],
                        "label": process_area_json[process_area],
                        "icon": "fa fa-sliders fa-rotate-90",
                        "show": True,
                        "search": True,
                        "id": process_area
                    }
                )
            # # Form Parent Section - Process Areas
            # side_bar_json["parent_sections"] = sorted(side_bar_json["parent_sections"], key=lambda k: k['label'])
            #
            # # Form Child Section - Steps
            # side_bar_json["childSections"] = sorted(side_bar_json["childSections"], key=lambda k: k['label'])

        # Form Response
        response['status'] = "OK"
        response['message'] = side_bar_json
        return response
    except Exception as e:
        print((traceback.format_exc()))
        logger.error(str(e))
        raise Exception(str(e))


def fetch_group_parameters(group_type, group):
    """
    This method fetches parameters related to a group
    :param group_type: Group Type - Site Parameter Set or General Parameter Set
    :param group: Group ID
    :return: Parameters related to that group
    """
    try:
        # Initialize
        parameters_list = []
        uom_id_list = []
        parameter_template_ids = []
        parameter_templates = {}
        uoms = {}

        # Fetch parameter group record
        group_record = canvas_instance_obj.fetch_parameter_group_record(group_type, group)

        # Fetch parameters list
        parameters_id_list = group_record["parameters"]

        # Fetch parameter records
        parameter_records = canvas_instance_obj.fetch_multiple_parameter_records(parameters_id_list)

        # Iterate through each parameter record and form uom and parameter template list
        for each_record in parameter_records:
            uom_id_list.append(each_record.get("uom", ""))
            parameter_template_ids.append(each_record["parameterTemplate"])

        # Fetch parameter template records
        template_records = canvas_instance_obj.fetch_multiple_parameter_template_records(parameter_template_ids)

        # Iterate through each parameter template record and form parameter template JSON
        for record in template_records:
            if record['id'] not in parameter_templates:
                parameter_templates[record['id']] = record

        # Fetch Unit of Measure records
        uom_records = canvas_instance_obj.fetch_multiple_uom_records(uom_id_list)

        # Iterate through each unit of measure records and form UoM JSON
        for record in uom_records:
            if record["id"] not in uoms:
                uoms[record["id"]] = record.get("UoM", "")

        # Iterate through each parameter records
        for record in parameter_records:
            # Fetch values for the parameter
            record["value"] = record.get('predefinedOptions', {})

            # Fetch fields related to the parameter
            record["fields"] = parameter_templates[record['parameterTemplate']]["fields"]

            # Fetch Unit of Measure name
            record["uomName"] = uoms.get(record.get('uom', ""), "")

            # adding calculation formula in fields
            calculation_formula = record.get('field_formula_list', {})
            param_fields = copy.deepcopy(record.get("fields", []))
            for field in param_fields:
                if field['fieldType'] == 'calculated_input':
                    field.update(calculation_formula.get(field.get('fieldId'), {}))

            record['fields'] = param_fields

            # Fetch Facility fit rules
            try:
                facility_fit_rules = parameter_templates[record['parameterTemplate']]["facility_fit_configuration"]
            except Exception as e:
                logger.error(str(e))
                facility_fit_rules = {}
            record["facility_fit_configuration"] = facility_fit_rules

            # Form Parameters list
            parameters_list.append(copy.deepcopy(record))
        return parameters_list
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def list_groups(group_type, group, record_id):
    """
    This method sends static JSON with key 'site_parameter_set and general_parameter_set' if there is no group type and
    group in request arguments, Sends group list if there is no group and only group type, sends parameter and
    parameter fields if both group and group type is available and sends all parameters from a specific parameter group
    if record id is set to 'all' and group type is specified
    :param group: Group ID
    :param group_type: Group
    :param record_id: Record id will be set to 'all' to fetch all parameters on a particular group or it will be null
    :return:
    """
    uom_list = []
    uom_json = dict()
    try:
        response = dict()
        response["content"] = {"groupList": []}

        param_type = {"general_parameter_set": "general", "site_parameter_set": "site"}

        # Fetch all parameters in general or site parameter based on parameter group set if record id is set to 'all'
        if record_id.lower() == "all":
            parameters_id_list = []
            parameters_list = []

            # if ER, list all parameters
            if group_type in ['experimental_parameter_set', 'master_parameter_set']:
                parameters_list_all = canvas_instance_obj.get_list_parameters()
                for each in parameters_list_all:
                    parameters_id_list.append(each["id"])
            else:
                parameter_groups = canvas_instance_obj.list_groups(group_type)
                for each_group in parameter_groups:
                    for each_parameter in each_group.get("parameters", []):
                        parameters_id_list.append(each_parameter)
            parameters_id_list = list(set(parameters_id_list))
            parameter_records = canvas_instance_obj.fetch_multiple_parameter_records(parameters_id_list)
            for each_record in parameter_records:
                # Form uom list
                uom_list.append(each_record.get("uom", ""))

            # Remove duplicates
            uom_list = list(set(uom_list))

            # Fetch measure records
            uom_records = canvas_instance_obj.fetch_multiple_uom_records(uom_list)

            # Form uom JSON
            for each_record in uom_records:
                uom_json[each_record["id"]] = each_record.get("UoM", "")

            for item in parameter_records:
                parameters_list.append({'id': item.get('id', ''), "parameterName": item.get("parameterName", ""),
                                        "uomName": "({uom_name})".format(
                                            uom_name=uom_json.get(item.get("uom", ""), ""))})
                parameters_list = sorted(parameters_list, key=lambda k: k.get('parameterName', '').lower())
            response["content"] = {"params": parameters_list}

        # This block returns a static response for the user to select site or general parameters set
        elif group_type == "" and group == "":
            response = get_canvas_static_jsons("canvas_items_list_groups")

        # This method fetches all the groups associated to a particular set
        elif group == "":
            group_list = []
            group_list_gr = []
            group_list_sr = []
            # if ER, list all groups in GR and SR with labels
            if group_type in ['experimental_parameter_set', 'master_parameter_set']:
                group_records_gr = canvas_instance_obj.list_groups("general_parameter_set")
                for each_record_gr in group_records_gr:
                    group_list_gr.append({"id": each_record_gr["id"], "itemName": each_record_gr["groupName"] + " (GR)"})
                group_records_sr = canvas_instance_obj.list_groups("site_parameter_set")
                for each_record_sr in group_records_sr:
                    group_list_sr.append({"id": each_record_sr["id"], "itemName": each_record_sr["groupName"] + " (SR)"})
                group_list = group_list_gr
                group_list.extend(group_list_sr)
            else:
                group_records = canvas_instance_obj.list_groups(group_type)
                for each_record in group_records:
                    group_list.append({"id": each_record["id"], "itemName": each_record["groupName"]})
            group_list = sorted(group_list,
                                key=lambda k: k.get('itemName', '').lower())
            response["content"] = {"groupList": group_list}

        # This method fetches all parameters associated to a particular group
        else:
            params_list = []
            params_records = fetch_group_parameters(group_type, group)
            for each_record in params_records:
                value = each_record.get("value", [])
                new_parameter_values = convert_parameter_label_and_value(value)
                parameter_name = each_record["parameterName"]
                parameter_key = parameter_name.replace(" ", "_").lower()
                params_list.append({
                    "id": each_record.get("id", ""),
                    "paramType": param_type.get(group_type, ""),
                    "param_key": parameter_key,
                    "param_label": parameter_name,
                    "uom": each_record.get("uomName", ""),
                    "fields": each_record.get("fields", []),
                    "param_desc": each_record.get("description", ""),
                    "value": new_parameter_values,
                    "facility_fit_configuration": each_record.get("facility_fit_configuration", {}),
                    "selectedValue": {
                        "text": "",
                        "drop_down": [],
                        "radio_box": "",
                        "check_box": {}
                    }
                })
            response["content"] = {"paramsList": params_list}
        return response
    except Exception as e:
        logger.error(traceback.format_exc())
        logger.error(str(e))
        raise Exception(str(e))


def list_multiple_groups(group_type, parameter_groups_list):
    try:
        parameter_templates = {}
        uoms = {}
        response = {}
        uom_id_list = []
        parameters_list = []
        parameters_id_list = []
        parameter_group_records = canvas_instance_obj.fetch_multiple_parameter_group_records(parameter_groups_list,
                                                                                             group_type)
        parameter_group_mapping_json = {}
        for each_record in parameter_group_records:

            # Fetch parameters list
            parameters_id_list += each_record.get("parameters", [])
            for each_parameter in each_record.get("parameters", []):
                if each_parameter not in parameter_group_mapping_json:
                    parameter_group_mapping_json[each_parameter] = each_record.get("groupName", "")
        parameters_id_list = list(set(parameters_id_list))
        parameter_records = canvas_instance_obj.fetch_multiple_parameter_records_for_parameter_group(parameters_id_list)
        # Iterate through each parameter record and form uom and parameter template list
        for each_record in parameter_records:
            uom_id_list.append(each_record.get("uom", ""))

        # Fetch Unit of Measure records
        uom_records = canvas_instance_obj.fetch_multiple_uom_records(uom_id_list)

        # Iterate through each unit of measure records and form UoM JSON
        for record in uom_records:
            if record["id"] not in uoms:
                uoms[record["id"]] = record.get("UoM", "")
        for each_record in parameter_records:
            parameters_list.append({
                "id": each_record.get("id", ""),
                "param_label": each_record.get("parameterName"),
                "uom": uoms.get(each_record.get("uom", ""), ""),
                "group_name": parameter_group_mapping_json.get(each_record.get('id', ""))
            })
        response["content"] = {"paramsList": parameters_list}
        return response
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def fetch_parameter_list(group_type, parameters_id_list):
    try:
        parameter_templates = {}
        uoms = {}
        response = {}
        uom_id_list = []
        param_records = []
        parameters_list = []
        parameter_template_ids = []
        param_type = {"general_parameter_set": "general", "site_parameter_set": "site"}

        parameter_records = canvas_instance_obj.fetch_multiple_parameter_records(parameters_id_list)
        # Iterate through each parameter record and form uom and parameter template list
        for each_record in parameter_records:
            uom_id_list.append(each_record.get("uom", ""))
            parameter_template_ids.append(each_record["parameterTemplate"])

        # Fetch parameter template records
        template_records = canvas_instance_obj.fetch_multiple_parameter_template_records(parameter_template_ids)

        field_id_list = []
        # Iterate through each parameter template record and form parameter template JSON
        for record in template_records:
            if record['id'] not in parameter_templates:
                parameter_templates[record['id']] = record

            for each_field in record.get('fields', ''):
                field_id_list.append(each_field.get('fieldId', ''))

        parameter_attributes_json = {}
        parameter_attribute_records = \
            canvas_instance_obj.fetch_multiple_parameter_attribute_records(list(set(field_id_list)))
        for each_record in parameter_attribute_records:
            parameter_attributes_json[each_record.get("id", "")] = each_record

        # Fetch Unit of Measure records
        uom_records = canvas_instance_obj.fetch_multiple_uom_records(uom_id_list)

        # Iterate through each unit of measure records and form UoM JSON
        for record in uom_records:
            if record["id"] not in uoms:
                uoms[record["id"]] = record.get("UoM", "")

        # Iterate through each parameter records
        for record in parameter_records:
            # Fetch values for the parameter
            # record["value"] = record.get('predefinedOptions', {})

            # Fetch fields related to the parameter
            record["fields"] = parameter_templates[record['parameterTemplate']]["fields"]

            # Fetch Unit of Measure name
            record["uomName"] = uoms.get(record.get('uom', ""), "")

            # adding calculation formula in fields
            calculation_formula = record.get('field_formula_list', {})
            param_fields = copy.deepcopy(record.get("fields", []))
            for field in param_fields:
                field.update(parameter_attributes_json.get(field.get('fieldId', ''), {}))
                if field['fieldType'] == 'calculated_input':
                    field.update(calculation_formula.get(field.get('fieldId'), {}))

            record['fields'] = param_fields

            # Fetch Facility fit rules
            try:
                facility_fit_rules = parameter_templates[record['parameterTemplate']][
                    "facility_fit_configuration"]
            except Exception as e:
                logger.error(str(e))
                facility_fit_rules = {}
            record["facility_fit_configuration"] = facility_fit_rules

            # Form Parameters list
            param_records.append(copy.deepcopy(record))

        for each_record in param_records:
            new_parameter_values = {}
            for each_field in each_record.get("fields", []):
                if each_field.get('fieldType') in ["drop_down", "drop_down_multiselect", "radio_box", "check_box"]:
                    new_parameter_values[each_field['fieldId']] = []
                    for each_tag in each_field.get('predefinedOptions', []):
                        new_parameter_values[each_field['fieldId']].append(
                            {"id": each_tag.get("label"), "itemName": each_tag.get("value")})

            parameter_name = each_record["parameterName"]
            parameter_key = parameter_name.replace(" ", "_").lower()
            parameters_list.append({
                "id": each_record.get("id", ""),
                "paramType": param_type.get(group_type, ""),
                "param_key": parameter_key,
                "param_label": parameter_name,
                "uom": each_record.get("uomName", ""),
                "fields": each_record.get("fields", []),
                "param_desc": each_record.get("description", ""),
                "value": new_parameter_values,
                "facility_fit_configuration": each_record.get("facility_fit_configuration", {}),
                "selectedValue": {
                    "text": "",
                    "drop_down": [],
                    "radio_box": "",
                    "check_box": {}
                }
            })
        response["content"] = {"paramsList": parameters_list}
        return response
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def fetch_materials_list(material_id_list):
    """
    This method fetches all material sets if Group ID is empty. If a Group is provided it fetches all the materials
    associated with that group.
    :param group: Material Group
    :return: Material Sets if Group ID is empty, else Materials if Group ID is provided
    """
    try:
        response = {"content": {}}

        recipe_list = []
        recipe_json = {}
        measure_list = []
        measure_json = {}
        workspace_json = {}
        material_to_recipe_json = {}
        material_names_list = []

        # Fetch material records
        material_records = canvas_instance_obj.fetch_multiple_material_records(material_id_list)

        for each_record in material_records:
            for key, value in each_record.items():
                if key in ["material_concentration_unit", "molecular_weight_unit", "unit"]:
                    measure_list.append(value)
            material_names_list.append(each_record.get("materialID", ""))
        material_to_recipe_records = \
            canvas_instance_obj.fetch_multiple_material_to_recipe_records_using_material_id(material_id_list, material_names_list)

        for each_record in material_to_recipe_records:
            material_to_recipe_json[each_record["materialRowId"] or each_record["materialID"]] = each_record
            recipe_list.append(each_record.get('recipeId', ''))
        recipe_records = canvas_instance_obj.fetch_multiple_recipe_records(recipe_list)

        latest_workspace_records = canvas_instance_obj.fetch_latest_workspace_records(recipe_list)

        for each_record in latest_workspace_records:
            workspace_json[each_record["_id"]] = each_record

        for each_record in recipe_records:
            recipe_json[each_record["id"]] = each_record

        for each_record in material_records:
            for key, value in each_record.items():
                if key in ["material_concentration_unit", "molecular_weight_unit", "unit"]:
                    measure_list.append(value)

        measures_records = canvas_instance_obj.fetch_multiple_uom_records(measure_list)
        for each_record in measures_records:
            measure_json[each_record["id"]] = each_record

        for material in material_records:

            for key, value in material.items():
                if key in ["material_concentration_unit", "molecular_weight_unit", "unit"]:
                    material[key] = [{"id": value, "itemName": measure_json.get(value, {}).get("UoM", "")}]

            try:
                material['unit_name'] = material.get('unit', [])[0].get('itemName')
            except Exception as ex:
                logger.error(str(ex))
            if material.get("id", "") not in material_to_recipe_json and material.get("materialID", "") in material_to_recipe_json:
                material_id = material.get("materialID", "")
            material['composition'] = fetch_material_compostion_details(material.get('id'))
            material["recipe_name_tooltip"] = recipe_json.get(material_to_recipe_json.get(
                material_id, {}).get("recipeId", ""), {})
            material["recipe_name"] = \
                recipe_json.get(material_to_recipe_json.get(material_id, {}).get(
                "recipeId", ""), {}).get("processName", "")
            material["recipe_version"] = ""
            if material["recipe_name"]:
                latest_version_json = {
                    "accessed_ts": workspace_json.get(material_to_recipe_json.get(material_id, {}).get(
                        "recipeId", ""), {}).get("accessed_ts", ""),
                    "version": workspace_json.get(material_to_recipe_json.get(material_id, {}).get(
                        "recipeId", ""), {}).get("version_label", ""),
                    "recipeId": recipe_json.get(material_to_recipe_json.get(material_id, {}).get(
                        "recipeId", ""), {}).get("id", ""),
                    "workspaceId": workspace_json.get(material_to_recipe_json.get(material_id, {}).get(
                        "recipeId", ""), {}).get("workspace_id", ""),
                    "modality_code": recipe_json.get(material_to_recipe_json.get(material_id, {}).get(
                        "recipeId", ""), {}).get("productFamilyName", ""),

                    "modality_id": recipe_json.get(material_to_recipe_json.get(material_id, {}).get(
                        "recipeId", ""), {}).get("productFamilyId", ""),
                    "process_name": recipe_json.get(material_to_recipe_json.get(material_id, {}).get(
                        "recipeId", ""), {}).get("processName", ""),
                    "process_type": recipe_json.get(material_to_recipe_json.get(material_id, {}).get(
                        "recipeId", ""), {}).get("processType", ""),
                    "process_folder": recipe_json.get(material_to_recipe_json.get(material_id, {}).get(
                        "recipeId", ""), {}).get("selectedFilePath", ""),
                    "selectedWorkspaceType": recipe_json.get(
                        material_to_recipe_json.get(material_id, {}).get(
                            "recipeId", ""), {}).get("selectedWorkspaceType", "")
                }
                material["latest_version"] = latest_version_json
        response["content"] = material_records
        return response
    except Exception as e:
        logger.error(traceback.format_exc())
        logger.error(str(e))
        raise Exception(str(e))


def list_material_group(group):
    """
    This method fetches all material sets if Group ID is empty. If a Group is provided it fetches all the materials
    associated with that group.
    :param group: Material Group
    :return: Material Sets if Group ID is empty, else Materials if Group ID is provided
    """
    try:
        # Initialize
        response = {"content": {}}

        # If Group is not provided fetch all material sets available
        if group == "" or group is None:

            # Initialize
            group_list = []

            # Fetch material group records
            material_group_records = canvas_instance_obj.fetch_all_material_set_records()

            # Iterate through each material and form groups list
            for each_record in material_group_records:
                group_list.append({"id": each_record.get("id", ""),
                                   "itemName": each_record.get("groupName", "")})
            response["content"] = group_list

        # If Group is provided fetch the material group record
        else:
            # Fetch Material group record based on Group ID
            material_group_record = canvas_instance_obj.fetch_material_set_record(group)
            # Fetch materials associated with a material group
            material_id_list = material_group_record.get("materials", [])
            # Fetch material records
            material_records = canvas_instance_obj.fetch_multiple_material_records_for_material_group(material_id_list)
            response["content"] = material_records
        return response
    except Exception as e:
        logger.error(traceback.format_exc())
        logger.error(str(e))
        raise Exception(str(e))


def fetch_material_compostion_details(material_id):
    """
    :param material_id:
    :return:
    """
    try:
        material_record = canvas_instance_obj.fetch_record_by_id(AppConfigurations.material_collection, material_id)

        def solution_component_data(material_record):
            if material_record.get('material_type', "") == 'solution':
                composition_id_list = []
                unit_id_list = []
                for composition in material_record.get('composition', []):
                    composition_id_list.append(composition.get('id'))
                    unit_id_list.append(composition.get('unit'))
                composition_records = canvas_instance_obj.fetch_multiple_material_records(composition_id_list)
                unit_records = canvas_instance_obj.fetch_multiple_unit_records(unit_id_list)
                composition_record_mapping = dict()
                unit_mappings = dict()
                for record in composition_records:
                    composition_record_mapping[record.get('id')] = record
                for record in unit_records:
                    unit_mappings[record.get('id')] = record
                for composition in material_record.get('composition', []):
                    if composition:
                        composition['material_name'] = composition_record_mapping.get(composition.get('id'), {}).get(
                            'material_name')
                        composition['materialID'] = composition_record_mapping.get(composition.get('id'), {}).get('materialID')
                        composition['unit'] = composition_record_mapping.get(composition.get('id'), {}).get('unit')
                        composition['material_type'] = composition_record_mapping.get(composition.get('id'), {}).get(
                            'material_type', "")
                        try:
                            composition['unit_name'] = \
                                canvas_instance_obj.fetch_multiple_unit_records([composition['unit']])[0].get('UoM')
                        except:
                            pass # Just passing
                        try:
                            composition['materialCompositionDetails'].update(
                                solution_component_data(composition_record_mapping.get(composition.get('id'), {})))
                        except Exception as ex:
                            logger.error(str(ex))
                            composition['materialCompositionDetails'] = solution_component_data(
                                composition_record_mapping.get(composition.get('id'), {}))
                return material_record.get('composition', [])
            else:
                return []

        return solution_component_data(material_record)
    except Exception as ex:
        raise Exception(str(ex))


def add_recipe_fields(input_js):
    """
    This method adds extra fields required for recipe collection
    :param input_js: Input JSON
    :return: Data after extra recipe fields added
    """
    try:
        input_js["modified_ts"] = str(datetime.utcnow()).split('.')[0]
        input_js["created_ts"] = str(datetime.utcnow()).split('.')[0]
        return input_js
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def update_recipe_fields(input_js):
    """
    This method adds extra fields required for recipe collection
    :param input_js: Input JSON
    :return: Data after extra recipe fields added
    """
    try:
        input_js["modified_ts"] = str(datetime.utcnow()).split('.')[0]
        return input_js
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def create_recipe(recipe_data):
    """
    This method handles adding and updating recipe
    :param recipe_data: Recipe data
    :return: Message and Workspace ID
    """
    try:
        workspace_id = recipe_data.get("id") or canvas_instance_obj.generate_workspace_id()
        recipe_data["id"] = workspace_id
        recipe_data["accessed_ts"] = str(datetime.utcnow()).split('.')[0]
        location = "{}{}".format(recipe_data["userId"], recipe_data["selectedFilePath"])
        message = {"status": "OK", "message": "Success: Your version has been saved in " + location}
        process_name = recipe_data["processName"]
        if recipe_data["type"] == "update":
            recipe_id = recipe_data["recipeId"]
            if canvas_instance_obj.check_deleted_recipe(recipe_id):
                warning_message = "Unable to Save. Recipe {}.{} does not exist!".format(process_name, "ps")
                message = error_obj.result_error_template(message=warning_message, error_category="Warning")
            else:
                canvas_instance_obj.insert_workspace_record(workspace_id, recipe_data)
                recipe_record = update_recipe_fields({})
                canvas_instance_obj.insert_recipe_record(recipe_id, recipe_record)
                message["id"] = workspace_id
        elif recipe_data["type"] == "add":
            if canvas_instance_obj.check_recipe_exists(recipe_data):
                warning_message = str(AppConstants.CanvasConstants.recipe_already_exists).format(process_name, "ps")
                message = error_obj.result_error_template(message=warning_message, error_category="Warning")
            else:
                recipe_id = recipe_data["recipeId"]
                recipe_record = canvas_instance_obj.fetch_recipe_record(recipe_id)
                new_recipe_id = canvas_instance_obj.generate_recipe_id()
                recipe_record["id"] = new_recipe_id
                recipe_record["processName"] = recipe_data["processName"].replace(".ps", "")
                recipe_record["selectedFilePath"] = recipe_data["selectedFilePath"]
                recipe_record["selectedWorkspaceType"] = recipe_data["selectedWorkspaceType"]
                recipe_record["recipeType"] = recipe_data["recipeType"]
                recipe_record["userId"] = recipe_data.get("userId", "")
                recipe_record = add_recipe_fields(recipe_record)
                if recipe_record.get("archive", False) is True:
                    recipe_record.pop("archive", None)
                canvas_instance_obj.insert_recipe_record(new_recipe_id, recipe_record)
                recipe_data["recipeId"] = new_recipe_id
                canvas_instance_obj.insert_workspace_record(workspace_id, recipe_data)
        return message, workspace_id
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def add_recipe(workspace_data):
    """
    This method is for inserting a recipe
    :param workspace_data: Workspace Data
    :return:
    """
    try:
        recipe_or_folder_type = "recipe"
        input_json = workspace_data["payload"]
        input_json['processName'] = input_json.get('processName', "").strip()
        recipe_data = workspace_data.get("payload", {})
        logger.debug("Recipe Data == >" + str(recipe_data))
        if input_json.get("type", "") == "update":
            type_ = "edit"
            input_json['recipeObj'] = remove_comment_status_from_recipe(input_json.get('recipeObj', {}))
        elif input_json.get("type", "") == "save":
            type_ = "save"
        else:
            type_ = "add"
            input_json['recipeObj'] = remove_comment_status_from_recipe(input_json.get('recipeObj', {}))
        logger.debug(str(AppConstants.CanvasConstants.type_logger) + str(type_))
        logger.debug(str(AppConstants.CanvasConstants.recipe_or_folder_type_logger) + str(recipe_or_folder_type))
        message, workspace_id, version = canvas_instance_obj.add_recipe(input_json)
        input_json["version"] = version
        RecentProcessAC.add_recent_process(input_json, workspace_id, input_json.get("recipeId", ""))
        if message.get("status", "") == "OK":
            AuditManagementAC.save_audit_entry()
        # AuditManagementAC.save_audit_entry("admin", "", {}, "recipe")
        return message
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def delete_folder(recipe_data):
    """
    This method is for deleting the folder and its contents
    :param recipe_data: Contains detailed information about the folder
    """
    try:
        # Delete multiple recent processes which are connected to that folder
        status = "warning"
        deleted_records = []
        archive_recipe_list = []
        accessible_recipe_list = []
        recipe_records = canvas_instance_obj.fetch_shared_recipes_for_deletion(recipe_data)
        published_recipe_records, accessible_recipe_records = canvas_instance_obj.fetch_accessible_shared_recipes(
            recipe_data)
        folder_to_recipe_mapping_json = {"hierarchyStructure": {}, "folders": {}}
        recipe_list = []
        for each_record in accessible_recipe_records:
            recipe_list.append(each_record.get("id", ""))
        workflow_review_records = canvas_instance_obj.fetch_multiple_active_workflow_records_using_recipe_id(
            recipe_list)
        if not published_recipe_records and len(recipe_records) == len(accessible_recipe_records) and not \
                workflow_review_records:
            for each_record in accessible_recipe_records:
                accessible_recipe_list.append(each_record.get("id", ""))
            for each_record in recipe_records:
                if not each_record.get("processName", "") and each_record.get(
                        "selectedFilePath", "") not in folder_to_recipe_mapping_json:
                    folder_to_recipe_mapping_json["hierarchyStructure"][each_record.get("selectedFilePath", "")] = []
                    folder_to_recipe_mapping_json["folders"][each_record.get("selectedFilePath", "")] = each_record
                    each_record["delete"] = True
            for each_record in recipe_records:
                for each_folder in folder_to_recipe_mapping_json.get("hierarchyStructure", {}):
                    if each_record.get("selectedFilePath", "").startswith(each_folder) and (
                            each_folder != each_record.get(
                            "selectedFilePath", "") or each_record.get("processName")):
                        folder_to_recipe_mapping_json["hierarchyStructure"][each_folder].append(each_record)
                        each_record["delete"] = True
            full_permission = True
            for folder_path, recipes in folder_to_recipe_mapping_json.get("hierarchyStructure", {}).items():
                count = 0
                for each_recipe in recipes:
                    if each_recipe.get("delete", ""):
                        archive_recipe_list.append(each_recipe.get("id", ""))
                        count += 1
                if count == len(recipes):
                    archive_recipe_list.append(
                        folder_to_recipe_mapping_json.get("folders", {}).get(folder_path, {}).get(
                            "id", ""))
                else:
                    full_permission = False

            if not full_permission:
                archive_recipe_list = []

            if archive_recipe_list:
                archive_recipe_list = list(set(archive_recipe_list))
                RecentProcessAC.delete_multiple_recent_process_using_recipe_id(archive_recipe_list)
                deleted_records = canvas_instance_obj.delete_multiple_recipe_and_workspace_records_in_recipe_collection(
                    archive_recipe_list)
                start_new_thread(
                    canvas_instance_obj.delete_multiple_recipe_and_workspace_records_except_recipe_collection,
                    (archive_recipe_list,))
                status = "success"

        return deleted_records, status
    except Exception as e:
        logger.error(traceback.format_exc())
        logger.error(str(e))
        raise Exception(str(e))


def delete_recipe(recipe_data, user_role_code_list):
    """
    This method is for deleting a recipe
    :param recipe_data: Contains detailed information about a recipe or a folder
    :param user_role_code_list: User Roles Code List
    :return: Message whether recipe or folder is successfully deleted or not
    """
    try:
        type_ = "delete"
        response = {}
        logger.debug(str(AppConstants.CanvasConstants.type_logger) + str(type_))

        recipe_type = recipe_data.get("recipeType", "")
        logger.debug("Recipe Type == >" + str(recipe_type))

        # Fetch Process Name
        process_name = recipe_data.get("processName", "")

        # Check if the deleting content is recipe or an folder
        if recipe_data.get("resource_type", "folder") == "folder":

            # Fetch Folder Name
            folder_name = recipe_data.get("selectedFilePath", "").split("/")[-1]
            # Call Delete Folder Method
            deleted_records, status = delete_folder(recipe_data)

            logger.debug("Deleted Records ==> " + json.dumps(deleted_records))
            # Form Recipe Details JSON
            recipe_details = {"itemName": folder_name, "selectedFilePath": recipe_data.get("selectedFilePath", ""),
                              "resource_type": "folder"}

            if status == "success":

                response = {"status": "OK", "message": "Successfully deleted the folder",
                            "recipeDetails": recipe_details}
            elif status == "warning":

                warning_message = "Unable to delete the Folder," \
                                  " Unauthorized to Perform the Action or Recipe has Published" \
                                  "/Approved/Major Version in its Lineage or the Recipe has any Active Workflow"
                response = error_obj.result_error_template(message=warning_message, error_category="Warning")
                response["recipeDetails"] = recipe_details
        else:

            # Fetch recipe ID
            recipe_id = recipe_data.get("recipeId", "")

            published_recipe_records = canvas_instance_obj.fetch_published_records_using_recipe_id(recipe_id)
            accessible_recipe_records = canvas_instance_obj.fetch_accessible_shared_recipes_using_recipe_id(
                recipe_data)

            active_workflow_review_records = canvas_instance_obj.fetch_active_workflow_review_records_using_recipe_id(
                recipe_id)

            # Form Recipe Details JSON
            recipe_details = {"itemName": process_name, "selectedFilePath": recipe_data.get("selectedFilePath", ""),
                              "resource_type": "folder"}

            if not published_recipe_records and accessible_recipe_records and not active_workflow_review_records:
                # Fetch File Path
                file_path = recipe_data.get("selectedFilePath", "")

                # Delete in recent process
                RecentProcessAC.delete_recent_process(recipe_id, process_name, file_path)

                # Delete recipe
                canvas_instance_obj.delete_recipe_in_recipe_collection(recipe_id, process_name, file_path)
                start_new_thread(canvas_instance_obj.delete_recipe_details_except_recipe_collection, (recipe_id,))
                response = {"status": "OK", "message": "Successfully deleted a recipe",
                            "recipeDetails": recipe_details}
            else:
                warning_message = "Unable to delete the recipe, Unauthorized to Perform the Action or Recipe has Published" \
                                  "/Approved/Major Version in its Lineage or the Recipe has any Active Workflow"
                response = error_obj.result_error_template(message=warning_message, error_category="Warning")
                response["recipeDetails"] = recipe_details

        recipe_hierarchy_input_json = {"userId": recipe_data.get("userId", ""), "recipeType": recipe_data.get(
            "recipeType", ""), "searchKey": recipe_data.get("searchKey", ""),
                                       "searchField": recipe_data.get("searchField", {}),
                                       "selectedFilePath": recipe_data.get("currentPath", "")}

        hierarchy_details = fetch_all_recipes_temp3(recipe_hierarchy_input_json, user_role_code_list)
        recipe_details["hierarchyDetails"] = hierarchy_details
        if response.get("status", "").lower() == "ok":
            AuditManagementAC.save_audit_entry()
        return response
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def remove_facility_fit_for_recipes(workspace_data):
    """
    This method removes step and activity colouring happened on facility fit while default loading recipe
    :return: Workspace data
    """
    try:
        step_data = workspace_data.get("recipeObj", {}).get("defaultData", {}).get("unitops", [])
        for each_step in step_data:
            each_step["step_not_available"] = False
            each_step["activity_design"] = {}
        return workspace_data
    except Exception as e:
        logger.error((traceback.format_exc()))
        logger.error(str(e))
        raise Exception(str(e))


def delete_unrelated_equipment(workspace_data):
    """
    This method is for removing equipment which are not having corresponding equipment class and Equipment class type
    and Equipment Type does not match
    :param workspace_data: Workspace data
    :return: Workspace Data after removing equipment
    """
    try:
        # Initialize
        omitted_step_keys = ["defaultData", "processFlowImg"]
        omitted_activity_keys = ["equipments_summary", "equipment_class_summary", "solution_class_summary"]

        # Iterate through each step
        for each_step in workspace_data.get("recipeObj", {}):

            # Check if each step is not in omitted step keys
            if each_step not in omitted_step_keys:

                # Iterate through each activity
                for each_activity in workspace_data.get("recipeObj", {}).get(each_step, {}).get("activityParams", {}):

                    # Initialize
                    equipment_class_id_list = []
                    equipment_class_mapping_json = dict()

                    # Iterate through each activity
                    if each_activity not in omitted_activity_keys:

                        # Fetch Equipment
                        equipment_list = workspace_data.get("recipeObj", {}).get(each_step, {}).get(
                            "activityParams", {}).get(each_activity, {}).get("equipmentParameters", [])

                        # Fetch Equipment Classes
                        equipment_class_list = workspace_data.get("recipeObj", {}).get(each_step, {}).get(
                            "activityParams", {}).get(each_activity, {}).get("equipParams", [])

                        # Iterate through each equipment class and form equipment class ID list and mapping json
                        for each_equipment_class in equipment_class_list:
                            equipment_class_id_list.append(each_equipment_class.get("equipmentClassId", ""))
                            equipment_class_mapping_json[each_equipment_class.get("equipmentClassId", "")] = \
                                each_equipment_class

                        # Check if each equipment type and their corresponding equipment class type matches
                        # If not remove it
                        try:
                            # Check if each equipment has an associated equipment class
                            # If not remove it
                            equipment_list[:] = [each_equipment for each_equipment in equipment_list if
                                                 each_equipment.get('equipmentClassId') in equipment_class_id_list]
                        except Exception as e:
                            logger.error(str(e))

        return workspace_data
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))
    
    
def form_notes_as_read_only(workspace_data):
    try:
        for each_step in workspace_data.get("recipeObj", {}).get("defaultData", []).get("unitops", []):
            if each_step.get("notes", ""):
                each_step[AppConstants.ServiceConstants.has_notes_show_read_only] = True
            else:
                each_step[AppConstants.ServiceConstants.has_notes_show_read_only] = False
    except Exception as e:
        logger.error(str(e))
    return workspace_data


def fetch_recipe(workspace_id, user_id, status=False, workspace_template_id=None):
    """
    :param workspace_id:
    :param user_id:
    :param status:
    :param workspace_template_id:
    :return:
    """
    try:
        # Initialize
        response = dict()
        user_records_mapping_json = dict()

        # for individual service
        wid = workspace_id

        # Fetch Workspace data
        workspace_data = canvas_instance_obj.fetch_recipe(workspace_id)
        view_status = workspace_data.get("viewer_status", False)
        
        logger.debug("Status " + str(status))
        
        # Linked Recipe
        linked_recipe_list = workspace_data.get("linked_recipe", [])
        if not linked_recipe_list:
            linked_recipe_list = [
                {
                    "id": "no_linked_recipe",
                    "itemName": "No Linked Recipe"
                }
            ]
        else:
            linked_recipe_list = [
                {
                    "id": linked_recipe_list[0]["id"],
                    "itemName": linked_recipe_list[0]["itemName"]
                }
            ]

        # Remove Facility Fit Calculations in Workspace
        workspace_data = remove_facility_fit_for_recipes(workspace_data)
        recipe_id = workspace_data.get("recipeId", "")
        recipe_record = canvas_instance_obj.fetch_recipe_record(recipe_id)

        material_to_recipe_record = canvas_instance_obj.fetch_material_to_recipe_record_on_condition(
            {"recipeId": recipe_id})
        
        material_record = canvas_instance_obj.fetch_material_record_using_record_id(material_to_recipe_record.get(
            "materialRowId", ""))

        workspace_template_record = canvas_instance_obj.fetch_recipe(workspace_data.get("workspaceTemplateId", ""))
        latest = False

        modality_id = recipe_record.get("productFamilyId", "")

        latest_version_info = canvas_instance_obj.get_latest_version_info(recipe_id)
        if latest_version_info.get('id') != workspace_data['id']:
            try:
                workspace_data['workFlowReviewObj'].update({"editable": False})
            except Exception as ex:
                logger.debug(str(ex))
                workspace_data['workFlowReviewObj'] = {"editable": False}
        else:
            try:
                workspace_data['workFlowReviewObj'].update({"editable": True})
            except Exception as ex:
                logger.debug(str(ex))
            latest = True

        if workspace_data.get('recipeType', "").lower() == 'shared':
            # check edit access for a recipe
            view_only = True
            for user_group in recipe_record.get('userGroups', []):
                if user_group.get('roleId', "").lower() == "editor" and \
                     user_id in user_group.get('users', []):
                        view_only = False
                        break
            if latest and not view_only:
                workspace_data = CollaborationManagementAC.auto_checkin_steps(workspace_data, user_id)
                # update disable option for user if user is not already available
                if workspace_data.get('processType', '') == "site":
                    if 'disabledOptions' not in recipe_record:
                        recipe_record['disabledOptions'] = dict()
                    if 'grWorkspaceId' not in recipe_record['disabledOptions'].get(user_id, {}):
                        recipe_record['disabledOptions'][user_id] = {"grWorkspaceId": workspace_data.get('workspaceTemplateId')}
                        canvas_instance_obj.partial_update_record(recipe_id, recipe_record, AppConfigurations.recipe_collection)
            workspace_data = CollaborationManagementAC.view_selected_shared_workspace(workspace_data.get('id'), user_id)
            try:
                workspace_data['workFlowReviewObj']['view_only'] = view_only
            except Exception as ex:
                workspace_data['workFlowReviewObj'] = {"view_only": view_only}
            if not check_recipe_builder_access(user_id):
                workspace_data['workFlowReviewObj']['view_only'] = True
        workspace_data["version"] = workspace_data.get("version_label", "")
        workspace_data["modalityId"] = modality_id
        modality_record = canvas_instance_obj.fetch_modality_record(modality_id)
        workspace_data["modalityName"] = modality_record.get("modalityName", "")
        if 'sites' in list(recipe_record.keys()):
            workspace_data['sites'] = recipe_record.get('sites', [])
        user_groups = recipe_record.get('userGroups', [])
        recipe_owners_user_id_list = []
        recipe_owners_list = []
        try:
            for each_role in user_groups:
                if each_role.get("roleId", "").lower() == "owners":
                    recipe_owners_user_id_list = each_role.get("users", [])
            user_records = canvas_instance_obj.fetch_user_records_by_user_id(recipe_owners_user_id_list)
            for each_record in user_records:
                user_records_mapping_json[each_record.get("user_id")] = each_record

            for each_user in recipe_owners_user_id_list:
                if each_user in user_records_mapping_json:

                    recipe_owners_list.append("{first_name} {last_name} ({user_id})".format(
                        first_name=user_records_mapping_json.get(each_user, {}).get("first_name"),
                        last_name=user_records_mapping_json.get(each_user, {}).get("last_name"),
                        user_id=each_user))
                else:
                    recipe_owners_list.append("{user_id}".format(user_id=each_user))
        except Exception as ex:
            logger.error(str(ex))
            logger.error("unable to fetch owner details")
        recipe_owners = ",".join(str(x) for x in recipe_owners_list)
        free_text = True
        if not recipe_owners:
            recipe_owners = workspace_data.get("workFlowReviewObj", {}).get("recipeOwners", "")

        if "workFlowReviewObj" in workspace_data:
            workspace_data["workFlowReviewObj"]["recipeOwners"] = recipe_owners
            workspace_data["workFlowReviewObj"]['materialID'] = material_to_recipe_record.get("materialID", "")
            workspace_data["workFlowReviewObj"]['material_name'] = material_record.get(
                "material_name", "") or material_to_recipe_record.get("material_name", "")
            workspace_data["workFlowReviewObj"]["materialRowId"] = material_to_recipe_record.get("materialRowId", "")
            workspace_data["workFlowReviewObj"]["linkedRecipe"] = linked_recipe_list
            workspace_data["workFlowReviewObj"]["generalRecipeTemplateName"] = workspace_template_record.get(
                "processName", "")
            workspace_data["workFlowReviewObj"]["generalRecipeTemplateVersion"] = workspace_template_record.get(
                "version_label", "")
            if len(workspace_template_record.get("version_label", "").split(".")) > 2:
                workspace_data["workFlowReviewObj"]["generalRecipeTemplateVersion"] = ".".join(
                    workspace_template_record.get(
                        "version_label", "").split(".")[:2])
            if material_to_recipe_record.get("materialRowId", ""):
                free_text = False

            workspace_data["workFlowReviewObj"]["freeText"] = free_text
            if workspace_data["processType"] == "experimental":
                
                recipe_id_json = {'recipeId':recipe_id}
                sites = fetch_recipe_sites(recipe_id_json)
                site_data = sites.get("siteData", "")
                locations_data = site_data.get("locationsData")
                sites_list = []
                for each_site in locations_data:
                    sites_list.append(each_site.get("siteCode",""))
                workspace_data['siteNames'] = sites_list
                workspace_data['Objective'] = recipe_record.get("Objective", "")
                workspace_data['productScale'] = recipe_record.get("productScale", "")

        if workspace_data.get('recipe_metadata_from_local', {}).get('updated') and recipe_record.get('metadata_updated_ts',
                                                                                      recipe_record.get(
                                                                                              "created_ts")) < workspace_data.get(
                'recipe_metadata_from_local', {}).get('updated_ts', ""):

            workspace_data['phaseDetails'].update(
                {"phaseId": workspace_data.get('recipe_metadata_from_local', {}).get("phaseId"),
                 "phaseName": workspace_data.get('recipe_metadata_from_local', {}).get("phaseName")})
            workspace_data["workFlowReviewObj"]['materialID'] = workspace_data.get('recipe_metadata_from_local', {}).get("materialID", "")
            workspace_data["workFlowReviewObj"]['material_name'] = workspace_data.get('recipe_metadata_from_local', {}).get(
                "material_name", "")
            workspace_data["workFlowReviewObj"]["materialRowId"] = workspace_data.get('recipe_metadata_from_local', {}).get("materialRowId", "")
            workspace_data['recipeDescription'] = workspace_data.get('recipe_metadata_from_local', {}).get('recipeDescription')
            if 'Objective' in workspace_data.get('recipe_metadata_from_local') and 'productScale' in workspace_data.get(
                    'recipe_metadata_from_local'):
                workspace_data['Objective']= workspace_data.get('recipe_metadata_from_local', {}).get('Objective')
                workspace_data['productScale'] = workspace_data.get('recipe_metadata_from_local', {}).get(
                    'productScale')

        workspace_data.pop("recipe_metadata_from_local", None)
        workspace_data['userGroups'] = user_groups
        if workspace_data.get("workFlowReviewObj", {}).get("state", "").lower() == "approved":
            workspace_data["workFlowReviewObj"]["editable"] = True
            workspace_data["workFlowReviewObj"]["reviewState"] = True
        try:
            workspace_data['recipeObj'] = remove_comment_status_from_recipe(workspace_data.get('recipeObj', {}))
            recipe_version = workspace_data.get('version')
            major_version = recipe_version.split('.')[0]
            version_string = ""
            recipe_id = workspace_data.get('recipeId')
            comments_records, users_list = canvas_instance_obj.fetch_all_comment_details(recipe_id, major_version,
                                                                                         version_string, user_id)
            workspace_data = RecipeCommentsAC.add_comment_status_in_workspace(workspace_data, comments_records)
        except Exception as ex:
            logger.error(str(ex))
            logger.error(traceback.format_exc())
        RecentProcessAC.update_recent_process(workspace_id, user_id)

        # change control enable status
        from bin.core.application.ChangeManagementAC import check_for_change_control_active
        workspace_data['isChangeControlApplicable'] = check_for_change_control_active(workspace_id)
        if workspace_data['isChangeControlApplicable']:
            if 'recipeChangeDecorators' not in workspace_data:
                workspace_data['recipeChangeDecorators'] = dict()
            workspace_data['recipeChangeDecorators']['configuration'] = AppConstants.ChangControlConstansts.change_control_configuration
        
        workspace_data = form_notes_as_read_only(workspace_data)
        workspace_data.update({"viewer_status": view_status})
        response["payload"] = workspace_data
        # delete_unrelated_equipment(workspace_data)
        try:
            data = {}
            new_steps_added = False
            if workspace_data.get('processType', '') == "site" and latest:
                data, workspace_id, new_steps_added = get_compare_screen_data_temp(wid, user_id)
                status, gr_workspace_data = get_gr_data_for_sr(wid, user_id)
                if status:
                    update_decorator_obj_for_parameter(gr_workspace_data, workspace_data)
                workspace_data['enableComparisonButton'] = new_steps_added
            for step_data in workspace_data.get('recipeObj', {}).get('defaultData', {}).get('unitops', []):
                if step_data['step_id'] in data.keys() and data.get(step_data['step_id'], []):
                    step_data["change_status"] = True
                    workspace_data['enableComparisonButton'] = True
                else:
                    step_data["change_status"] = False

        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(str(e))
            logger.error(traceback.format_exc())
        
        return response
    except Exception as e:
        logger.error(traceback.format_exc())
        logger.error(str(e))
        raise Exception(str(e))


def check_has_more_records(page_no, page_size, records_count):
    try:
        has_more = False
        if (((page_no - 1) * page_size) + page_size) < records_count:
            has_more = True
        return has_more
    except Exception as e:
        has_more = False
        logger.error(str(e))
        return has_more


def generate_pagination_uri_for_file_explorer(page_no, has_more):
    try:
        prev_page_uri = ""
        next_page_uri = ""
        param_list = (request.full_path).split('&')
        for each in param_list:
            if '?' in each:
                param_list += each.split('?')
                param_list.remove(each)
        uri = ([each for each in param_list if "pageNo=" in each])
        if uri and not page_no == 1:
            prev_page_uri = request.full_path.replace(uri[0], 'pageNo='+str(int(page_no)-1))
        if uri and has_more:
            next_page_uri = request.full_path.replace(uri[0], 'pageNo='+str(int(page_no)+1))
        return prev_page_uri, next_page_uri
    except Exception as e:
        # handling exception
        logger.error(str(e))
        raise Exception(str(e))
        

def fetch_latest_workspace_id(recipe_id):
    try:
        workspace_record = canvas_instance_obj.fetch_latest_workspace_id_using_record_id(recipe_id)
        return workspace_record.get("workspace_id", "")
    except Exception as e:
        logger.error(traceback.format_exc())
        logger.error(str(e))
        raise Exception(str(e))


def fetch_all_recipes_temp3(input_json, user_role_code_list):
    """
    This method is for fetching all the recipes and forming directory structure
    :param input_json: Input JSON containing Search Details
    :param user_role_code_list: User Role Code List
    :return: Response after forming directory structure with recipes
    """
    try:
        home = "General Recipes"
        material_mappings_json = dict()
        materials_list = []
        response = {"folder": [], "file": []}
        page_no = int(input_json.get("pageNo", 1))
        page_size = int(input_json.get("pageSize", 30))
        sort_fields = input_json.get("sort", "")
        hidden = input_json.get("hidden", True)
        scroll_count = 0
        add_scroll = False
        modality_records = canvas_instance_obj.fetch_all_modality_records()
        modailty_mapping_json = dict()
        for each_modailty in modality_records:
            modailty_mapping_json[each_modailty.get("id", "")] = each_modailty
        if (input_json.get("recipeType", "") == "version" and len(set(
                AppConstants.ServiceConstants.view_private_recipe_user_code_list) & set(
            user_role_code_list)) > 0) or (
                input_json.get("recipeType", "") == "version" and not AppConstants.ServiceConstants.USER_ROLES_ACTIVE):
            home = input_json.get("userId", "")

        elif input_json.get("recipeType", "") == "template":
            home = "General Recipes"

        elif input_json.get("recipeType", "") == "publish":
            home = "Site Recipes"

        elif input_json.get("recipeType", "") == "published_er":
            home = "Experimental Recipes"

        elif input_json.get("recipeType", "") == "published_mr":
            home = "Master Recipes"

        if input_json.get("recipeType", "") == "shared":
            enable_search = False
            file_path_json = {}

            if input_json.get("searchKey", "") or input_json.get("searchField", {}):
                enable_search = True

            if enable_search:
                # recipe_records, user_accessible_records_count = canvas_instance_obj.fetch_records_on_pagination_for_shared_recipes(
                #     input_json, page_no, page_size, sort_fields)
                complete_recipe_records, complete_records_count = canvas_instance_obj.fetch_records_on_pagination_for_shared_recipes(
                    input_json, page_no, page_size, sort_fields, complete=False, path_specific=False, search=True,
                    hidden=hidden)

            elif input_json.get("selectedFilePath", "") in [""]:
                complete_recipe_records = []
                recipe_records = canvas_instance_obj.fetch_all_recipe_records_on_recipe_type(input_json)
                for each_record in recipe_records:
                    owner = ""
                    process_type = each_record.get("processType", "general")
                    recipe_status = each_record.get("workflow_state", "")
                    recipe_version_label = each_record.get("version_label", "")
                    new_recipe_version_label = recipe_version_label
                    if recipe_version_label:
                        recipe_version_label_list = recipe_version_label.split(".")
                        if len(recipe_version_label_list) >= 2:
                            new_recipe_version_label = str(AppConstants.CanvasConstants.major_and_minor_version_without_v).format(
                                major_version=recipe_version_label_list[0],
                                minor_version=recipe_version_label_list[1]
                            )
                    for each_role in each_record.get("userGroups", []) or []:
                        if each_role.get("roleId", "") == "owners":
                            owner = ",".join(each_role.get('users', []))

                    if process_type == "general":
                        type_ = AppConstants.CanvasConstants.general_recipe_type
                    elif process_type == "experimental":
                        type_ = AppConstants.CanvasConstants.experiment_recipe_type
                    elif process_type =="master":
                        type_ = AppConstants.CanvasConstants.master_recipe_type

                    else:
                        type_ = AppConstants.CanvasConstants.site_recipe_type
                    response['file'].append({
                        "recipeId": each_record.get("id", ""),
                        "itemName": each_record.get("processName", ""),
                        "updated_by": each_record.get("updated_by", ""),
                        "owner": owner,
                        "phaseName": each_record.get("phaseDetails", {}).get("phaseName", ""),
                        "mat_id": each_record.get("materialID", ""),
                        "mat_name": material_mappings_json.get(each_record.get("materialRowId", ""), {}).get(
                            "material_name", "") or each_record.get("material_name", ""),
                        "modality": modailty_mapping_json.get(each_record.get("productFamilyId", ""), {}).get("modalityName", "") or each_record.get("productFamilyName", ""),
                        "modality_id": each_record.get("productFamilyId", ""),
                        "selectedWorkspaceType": each_record.get("selectedWorkspaceType", ""),
                        "dateModified": each_record.get("modified_ts", ""),
                        "recipeType": type_,
                        "recipeStatus": recipe_status,
                        "recipeVersion": new_recipe_version_label,
                        "findAt": each_record.get("selectedFilePath", "")
                    })
                response["home"] = home
                return response
            else:
                # recipe_records, user_accessible_records_count = canvas_instance_obj.\
                #     fetch_records_on_pagination_for_shared_recipes(input_json, page_no, page_size, sort_fields)
                complete_recipe_records, complete_records_count = canvas_instance_obj.\
                    fetch_records_on_pagination_for_shared_recipes(
                    input_json, page_no, page_size, sort_fields, complete=False, path_specific=True, search=False,
                    hidden=hidden)
                

            # for each_record in recipe_records:
            #     if each_record.get("selectedFilePath", "") not in file_path_json.keys():
            #         file_path_json[each_record.get("selectedFilePath", "")] = []
            #     file_path_json[each_record.get("selectedFilePath", "")].append(each_record.get("id", ""))

            file_path_json.pop('', None)
            
            for each_record in complete_recipe_records:
                materials_list.append(each_record.get("materialRowId", ""))
                
            materials_list = list(set(materials_list))
            material_records = canvas_instance_obj.fetch_multiple_material_records(materials_list)
            for each_record in material_records:
                material_mappings_json[each_record["id"]] = each_record
                
            recipe_version_list = []
            for each_record in complete_recipe_records:
                recipe_version_list.append({"recipeId": each_record.get("id", ""),
                                            "version_label": each_record.get("version_label")})
                
            workspace_records = canvas_instance_obj.fetch_workspace_records_on_version(recipe_version_list)
            
            recipe_workspace_mapping_json = {}
            for each_record in workspace_records:
                recipe_workspace_mapping_json[each_record.get("recipeId", "")] = each_record
                
            for each_record in complete_recipe_records:
                disable = True
                new_file_path = ""
                owner = ""
                recipe_status = each_record.get("workflow_state", "")
                recipe_version_label = each_record.get("version_label", "")
                new_recipe_version_label = recipe_version_label
                if recipe_version_label:
                    recipe_version_label_list = recipe_version_label.split(".")

                    if len(recipe_version_label_list) >= 2:
                        new_recipe_version_label = str(AppConstants.CanvasConstants.major_and_minor_version_without_v).format(
                            major_version=recipe_version_label_list[0],
                            minor_version=recipe_version_label_list[1]
                        )

                file_path = each_record.get("selectedFilePath", "/")
                process_type = each_record.get("processType", "general")

                if process_type == "general":
                    type_ = AppConstants.CanvasConstants.general_recipe_type
                elif process_type == "experimental":
                    type_ = AppConstants.CanvasConstants.experiment_recipe_type

                elif process_type == "master":
                    type_ = AppConstants.CanvasConstants.master_recipe_type
                else:
                    type_ = AppConstants.CanvasConstants.site_recipe_type

                if file_path == "/":
                    new_file_path = file_path

                    # If File path is not root directory and forward slash is present in front remove forward slash
                elif file_path.startswith("/") and len(file_path.split('/')) > 1:
                    new_file_path = file_path[1:]
                if each_record.get("processName", "").strip() != "":

                    for each_role in each_record.get("userGroups", []) or []:
                        if each_role.get("roleId", "") == "owners":
                            owner = ",".join(each_role.get('users', []))

                    if (each_record.get("selectedFilePath", "") == input_json.get(
                            "selectedFilePath", "") and not input_json.get("searchKey", "") and not input_json.get(
                        "searchField", {})) or (input_json.get("searchField", {}).get(
                            "itemName", "").lower() in each_record.get("processName", "").lower() and input_json.get(
                            "searchField", {}).get("itemName")) or (input_json.get("searchKey", "") or (input_json.get(
                            "searchField", {}) and not input_json.get('searchField', {}).get("itemName", ""))):
                        
                        # for each_file_path in file_path_json.keys():
                        #     if each_file_path.startswith(each_record.get("selectedFilePath", "")) and each_record.get(
                        #             "id", "") in file_path_json.get(each_file_path, []):
                        #         disable = False
                        #         break
                        
                        if each_record.get("viewer_status", False) is True or each_record.get("userId",
                                                                                              "") == input_json.get(
                                "userId", ""):
                            disable = False
                            
                        else:
                            for each_user_group in each_record.get("userGroups", []):
                                if each_user_group.get("roleId", "") in ["viewer", "editor"] and input_json.get(
                                        "userId", "") in each_user_group.get("users", []):
                                    disable = False
                        
                        if disable:
                            scroll_count += 1
                            
                        response['file'].append({
                            "recipeId": each_record.get("id", ""),
                            "itemName": each_record.get("processName", ""),
                            "updated_by": each_record.get("updated_by", ""),
                            "owner": owner,
                            "phaseName": each_record.get("phaseDetails", {}).get("phaseName", ""),
                            "mat_id": each_record.get("materialID", ""),
                            "mat_name": material_mappings_json.get(each_record.get("materialRowId", ""), {}).get(
                                "material_name", "") or each_record.get("material_name", ""),
                            "modality": modailty_mapping_json.get(each_record.get("productFamilyId", ""), {}).get("modalityName", "") or each_record.get("productFamilyName", ""),
                            "modality_id": each_record.get("productFamilyId", ""),
                            "selectedWorkspaceType": each_record.get("selectedWorkspaceType", ""),
                            "dateModified": each_record.get("modified_ts", ""),
                            "recipeType": type_,
                            "recipeStatus": recipe_status,
                            "recipeVersion": new_recipe_version_label,
                            "findAt": each_record.get("selectedFilePath", ""),
                            "disable": disable,
                            "workspaceId": recipe_workspace_mapping_json.get(each_record.get("id", ""), {}).get(
                                "id", "")
                        })

                else:
                    folder_path_split = each_record.get("selectedFilePath", "").split("/")
                    del folder_path_split[-1]
                    if len(folder_path_split) == 1:
                        folder_path = "/"
                    else:
                        folder_path = "/".join(folder_path_split)

                    folder_name = new_file_path.split("/")[-1]
                    if each_record.get("selectedFilePath", "") != input_json.get("selectedFilePath",
                                                                                 "") and input_json.get(
                        "searchKey", "").lower() in folder_name.lower() and input_json.get(
                            "searchField", {}).get("itemName", "").lower() in folder_name.lower():
                            
                        response['folder'].append({
                            "recipeId": each_record.get("id", ""),
                            "itemName": folder_name,
                            "updated_by": each_record.get("updated_by", ""),
                            "owner": owner,
                            "mat_id": "",
                            "mat_name": "",
                            "modality": "",
                            "dateModified": each_record.get("modified_ts", ""),
                            "recipeType": "",
                            "recipeStatus": "",
                            "recipeVersion": "",
                            "findAt": folder_path,
                            "disable": False
                        })

        else:
            if input_json.get("selectedFilePath", "") in [""]:
                recipe_records = canvas_instance_obj.fetch_all_recipe_records_on_recipe_type(input_json)
            else:
                recipe_records, complete_records_count = canvas_instance_obj.fetch_recipes_on_pagination(
                    input_json, page_no, page_size, sort_fields)
            
            recipe_versions_list = []
            
            for each_record in recipe_records:
                new_version_label = each_record.get("version_label")
                if input_json.get("recipeType", "") in ["template", "publish"]:
                    try:
                        new_version_label = "{major_version}.{minor_version}.{micro_version}".format(
                            major_version=each_record.get("version_label").split(".")[0],
                            minor_version="0",
                            micro_version="0"
                        )
                    except Exception as e:
                        logger.error(str(e))
                
                recipe_versions_list.append({"recipeId": each_record.get("id", ""),
                                            "version_label": new_version_label})
                materials_list.append(each_record.get("materialRowId", ""))
                                
            workspace_records = canvas_instance_obj.fetch_workspace_records_on_version(recipe_versions_list)

            recipe_workspace_mapping_json = {}
            for each_record in workspace_records:
                recipe_workspace_mapping_json[each_record.get("recipeId", "")] = each_record

            materials_list = list(set(materials_list))
            material_records = canvas_instance_obj.fetch_multiple_material_records(materials_list)
            for each_record in material_records:
                material_mappings_json[each_record["id"]] = each_record

            for each_record in recipe_records:
                try:
                    owner = ""
                    new_recipe_version_label = ""
                    recipe_status = ""
                    file_path = each_record.get("selectedFilePath", "/")
                    new_file_path = file_path
                    process_type = each_record.get("processType", "general")
                    if process_type == "general":
                        type_ = AppConstants.CanvasConstants.general_recipe_type
                    elif process_type == "experimental":
                        type_ = AppConstants.CanvasConstants.experiment_recipe_type

                    elif process_type =="master":
                        type_= AppConstants.CanvasConstants.master_recipe_type
                    else:
                        type_ = AppConstants.CanvasConstants.site_recipe_type

                    if input_json.get("recipeType", "") in ["version", "shared", "create_site", "er_template"]:
                        recipe_version_label = each_record.get("version_label", "")
                        new_recipe_version_label = recipe_version_label
                        recipe_status = each_record.get("workflow_state", "")
                        if recipe_version_label:
                            recipe_version_label_list = recipe_version_label.split(".")
                            if len(recipe_version_label_list) >= 2:
                                new_recipe_version_label = str(AppConstants.CanvasConstants.major_and_minor_version_without_v).format(
                                    major_version=recipe_version_label_list[0],
                                    minor_version=recipe_version_label_list[1]
                                )

                    elif input_json.get("recipeType", "") in ["template", "publish", "published_er", "published_mr"]:
                        new_recipe_version_label = str(AppConstants.CanvasConstants.major_and_minor_version).format(
                            major_version=each_record.get("published_details", {}).get("version", 0),
                            minor_version=0
                        )
                        recipe_status = "Approved"

                    if file_path == "/":
                        new_file_path = file_path

                        # If File path is not root directory and forward slash is present in front remove forward slash
                    elif file_path.startswith("/") and len(file_path.split('/')) > 1:
                        new_file_path = file_path[1:]

                    if each_record.get("processName", "").strip() != "":

                        for each_role in each_record.get("userGroups", []) or []:
                            if each_role.get("roleId", "") == "owners":
                                owner = ",".join(each_role.get('users', []))

                        if (each_record.get("selectedFilePath", "") == input_json.get(
                                "selectedFilePath", "") and not input_json.get("searchKey", "") and not input_json.get(
                            "searchField", {})) or (input_json.get("searchField", {}).get(
                                "itemName", "").lower() in each_record.get(
                                "processName", "").lower() and input_json.get(
                                "searchField", {}).get("itemName")) or (input_json.get("searchKey", "") or (
                                    input_json.get(
                                        "searchField", {}) and not input_json.get('searchField', {}).get("itemName",
                                                                                                         "")))\
                                or (input_json.get("selectedFilePath", "") in [""]):
                            response['file'].append({
                                "recipeId": each_record.get("id", ""),
                                "itemName": each_record.get("processName", ""),
                                "updated_by": each_record.get("updated_by", ""),
                                "owner": owner,
                                "phaseName": each_record.get("phaseDetails", {}).get("phaseName", ""),
                                "phaseId": each_record.get("phaseDetails", {}).get("id", ""),
                                "mat_id": each_record.get("materialID", ""),
                                "mat_name": material_mappings_json.get(each_record.get("materialRowId", ""), {}).get(
                                    "material_name", "") or each_record.get("material_name", ""),
                                "modality": modailty_mapping_json.get(each_record.get("productFamilyId", ""), {}).get("modalityName", "") or each_record.get("productFamilyName", ""),
                                "modality_id": each_record.get("productFamilyId", ""),
                                "selectedWorkspaceType": each_record.get("selectedWorkspaceType", ""),
                                "dateModified": each_record.get("modified_ts", ""),
                                "recipeType": type_,
                                "recipeStatus": recipe_status,
                                "recipeVersion": new_recipe_version_label,
                                "findAt": each_record.get("selectedFilePath", ""),
                                "disable": False,
                                "workspaceId": recipe_workspace_mapping_json.get(each_record.get("id", ""), {}).get(
                                    "id", "")
                            })
                    else:
                        folder_path_split = each_record.get("selectedFilePath", "").split("/")
                        del folder_path_split[-1]
                        if len(folder_path_split) == 1:
                            folder_path = "/"
                        else:
                            folder_path = "/".join(folder_path_split)

                        folder_name = new_file_path.split("/")[-1]
                        if each_record.get("selectedFilePath", "") != input_json.get("selectedFilePath",
                                                                                     "") and input_json.get(
                            "searchKey", "").lower() in folder_name.lower() and input_json.get(
                                "searchField", {}).get("itemName", "").lower() in folder_name.lower():
                            response['folder'].append({
                                "recipeId": each_record.get("id", ""),
                                "itemName": folder_name,
                                "updated_by": each_record.get("updated_by", ""),
                                "owner": owner,
                                "mat_id": "",
                                "mat_name": "",
                                "modality": "",
                                "dateModified": each_record.get("modified_ts", ""),
                                "recipeType": "",
                                "recipeStatus": "",
                                "recipeVersion": "",
                                "findAt": folder_path,
                                "disable": False
                            })
                except Exception as ex:
                    logger.error(str(ex))
                    logger.error(traceback.format_exc())
                    logger.error(traceback.format_exc())
        header_content = get_canvas_static_jsons("file_explorer_header_content")
        for each_item in header_content:
            if each_item.get("key", "") in input_json.get("sort", "") and '+' in input_json.get("sort", ""):
                each_item['sort_state'] = "asc"
            elif each_item.get("key", "") in input_json.get("sort", "") and '-' in input_json.get("sort", ""):
                each_item['sort_state'] = "desc"
        has_more = check_has_more_records(page_no, page_size, complete_records_count)
        
        if scroll_count == page_size:
            add_scroll = True
        
        prev_page_uri, next_page_uri = generate_pagination_uri_for_file_explorer(page_no, has_more)
        response.update({"pageSize": page_size, "pageNo": page_no, "hasMore": has_more,
                         "total": complete_records_count, "nextPageUri": next_page_uri,
                         "prevPageUri": prev_page_uri, "headerContent": header_content, "scroll": add_scroll})
        response["home"] = home
        return response
    except Exception as e:
        logger.error(traceback.format_exc())
        logger.error(str(e))
        raise Exception(str(e))



def rename_folder(input_json):
    try:
        recipe_file_path_mapping_json = {}
        old_file_path = input_json.get("oldFilePath", "")
        new_file_path = input_json.get("newFilePath", "")
        accessible_recipes_json = {"selectedFilePath": old_file_path, "userId": input_json.get("userId", ""),
                                   "recipeType": input_json.get("recipeType", "")}
        recipe_records = canvas_instance_obj.fetch_recipe_records_based_on_file_path(old_file_path,
                                                                                     input_json.get("recipeType", ""))
        published_recipe_records, accessible_recipe_records = canvas_instance_obj.fetch_accessible_shared_recipes(
            accessible_recipes_json)

        recipe_list = []
        for each_record in recipe_records:
            recipe_list.append(each_record.get("id", ""))

        active_workflow_records = canvas_instance_obj.fetch_multiple_active_workflow_records_using_recipe_id(
            recipe_list)

        if published_recipe_records or len(recipe_records) != len(accessible_recipe_records) or active_workflow_records:
            warning_message = "Unable to Rename the Folder, Unauthorized to Perform the Action or Recipe has Published" \
                              "/Approved/Major Version in its Lineage or the Recipe has any Active Workflow"
            response = error_obj.result_error_template(message=warning_message, error_category="Warning")
            return response

        folder_data = {"folderPath": input_json.get("newFilePath", ""), "recipeType": input_json.get(
            'recipeType', '')}

        folder_name = input_json.get("newFilePath", "").split("/")[-1]
        if check_folder_exists(folder_data):
            message = str(AppConstants.CanvasConstants.already_contains_folder_named).format(folder_name=folder_name)
            response = error_obj.result_error_template(message=message, error_category="Warning")
            return response

        for each_record in recipe_records:
            recipe_file_path_mapping_json[each_record["id"]] = each_record.get("selectedFilePath", "")

        for recipe_id, file_path in recipe_file_path_mapping_json.items():
            recipe_file_path_mapping_json[recipe_id] = file_path.replace(old_file_path, new_file_path, 1)

        canvas_instance_obj.update_multiple_recipe_names_for_recipe_collection(recipe_file_path_mapping_json)
        start_new_thread(canvas_instance_obj.update_multiple_recipe_names_except_recipe_collection, (
            recipe_file_path_mapping_json,))
        response = {"status": "OK", "message": "Successfully Renamed the Folder!"}
        return response
    except Exception as e:
        logger.error(traceback.format_exc())
        logger.error(str(e))
        raise Exception(str(e))


def rename_files(input_json):
    try:
        recipe_id = input_json.get("recipeId", "")
        new_recipe_name = input_json.get("newRecipeName", "")
        if canvas_instance_obj.check_recipe_exists({"processName": new_recipe_name,
                                                    "selectedFilePath": input_json.get("selectedFilePath", ""),
                                                    "userId": input_json.get("userId", ""),
                                                    "recipeType": input_json.get("recipeType", "")}):
            warning_message = "Recipe {}.{} already exists. Please Add a New Name.".format(new_recipe_name, "ps")
            response = error_obj.result_error_template(message=warning_message, error_category="Warning")
            return response

        accessible_shared_recipes_json = {"selectedFilePath": input_json.get("selectedFilePath", ""),
                                          "userId": input_json.get("userId", ""),
                                          "recipeType": input_json.get("recipeType", ""),
                                          "recipeId": recipe_id}

        published_recipe_records = canvas_instance_obj.fetch_published_records_using_recipe_id(recipe_id)
        accessible_recipe_records = canvas_instance_obj.fetch_accessible_shared_recipes_using_recipe_id(
            accessible_shared_recipes_json)

        active_workflow_records = canvas_instance_obj.fetch_active_workflow_review_records_using_recipe_id(recipe_id)

        if published_recipe_records or active_workflow_records or not accessible_recipe_records:
            warning_message = "Unable to Rename the Recipe, Unauthorized to Perform the Action or Recipe has Published" \
                              "/Approved/Major Version in its Lineage or the Recipe has any Active Workflow"
            response = error_obj.result_error_template(message=warning_message, error_category="Warning")
            return response

        canvas_instance_obj.update_recipe_names_for_recipe_collection(recipe_id, new_recipe_name)
        start_new_thread(canvas_instance_obj.update_recipe_names_except_recipe_collection,
                         (recipe_id, new_recipe_name,))
        response = {"status": "OK", "message": "Successfully Renamed Recipe!"}
        return response
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def rename_file_folders(input_json, user_role_code_list):
    """
    This method is for renaming recipes and folders
    :param input_json: Input JSON containing info about renaming recipes and folders
    :param user_role_code_list: User Role Code List
    :return: Response after forming directory structure with recipes
    """
    try:
        type_ = "rename"
        logger.debug(str(AppConstants.CanvasConstants.type_logger) + str(type_))
        if input_json.get("resource_type", "") == "folder":
            response = rename_folder(input_json)
        else:
            response = rename_files(input_json)

        recipe_json = {"userId": input_json.get("userId", ""), "recipeType": input_json.get("recipeType", ""),
                       "searchKey": input_json.get("searchKey", ""), "searchField": input_json.get("searchField", {}),
                       "selectedFilePath": input_json.get("selectedFilePath", "")}
        hierarchy_details = fetch_all_recipes_temp3(recipe_json, user_role_code_list)
        response["hierarchyDetails"] = hierarchy_details
        if response.get("status", "").lower() == "ok":
            AuditManagementAC.save_audit_entry()
        return response
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def submit_version(recipe_data):
    """
    This method is for submitting a site or generic recipe
    :param recipe_data: Recipe Data
    :return: Message if recipe is successfully submitted for approval
    """
    try:
        input_json = recipe_data["payload"]
        user_id = input_json['userId']
        email_id = AppConstants.ServiceConstants.admin_email_id
        file_path = input_json['selectedFilePath']
        data = remove_comment_status_from_recipe(input_json['recipeObj'])
        recipe_type = input_json.get('recipeType', '')
        modality_id = input_json.get('modalityId', '')
        process_name = input_json.get('processName', '')
        recipe_id = input_json.get('recipeId', '')
        process_type = input_json.get('processType', 'general')
        selected_workspace_type = input_json.get('selectedWorkspaceType', 'Private')
        workspace_template_version = input_json.get("workspaceTemplateVersion", "NA")
        workspace_template_id = input_json.get("workspaceTemplateId", "NA")
        workspace_template_name = input_json.get("workspaceTemplateName", "NA")
        submission_ts = str(datetime.utcnow()).split('.')[0]
        query = {}
        argument = {
            "user_id": user_id,
            "email_id": email_id,
            "file_path": file_path,
            "process_name": process_name,
            "process_type": process_type,
            "recipeId": recipe_id,
            "data": data,
            "recipe_type": recipe_type,
            "modality_id": modality_id,
            "submission_ts": submission_ts,
            "selected_workspace_type": selected_workspace_type,
            "query": query,
            "workspaceTemplateName": workspace_template_name,
            "workspaceTemplateId": workspace_template_id,
            "workspaceTemplateVersion": workspace_template_version
        }

        # Temporarily removed email validation - Based on requirements
        # Check if the approver mail id is valid or not
        # if canvas_instance_obj.check_approver_id(user_id, email_id):
        #     warning_message = AppConstants.ServiceConstants.APPROVER_WARNING_MSG
        #     response = error_obj.result_error_template(message=warning_message, error_category="Warning")
        #     return response

        # Check if a recipe already exists with same name in a specific location
        if canvas_instance_obj.check_recipe_exists(input_json):
            if canvas_instance_obj.check_recipe_exists_with_id(input_json):
                argument["type"] = "update"
                argument = canvas_instance_obj.submit_version(argument)
                argument = canvas_instance_obj.update_user_and_approver_records(argument)
            else:
                warning_message = str(AppConstants.CanvasConstants.recipe_already_exists).format(process_name, "ps")
                response = error_obj.result_error_template(message=warning_message, error_category="Warning")
                return response

        else:
            # Submit Version
            argument = canvas_instance_obj.submit_version(argument)

            # Update User and Approver Part
            argument = canvas_instance_obj.update_user_and_approver_records(argument)

        # Check if there is any error while updating user and approver information
        if argument.get("status", "").lower() == "error":
            response = error_obj.result_error_template(argument["message"])
            return response
        message = argument["message"]


        if message == "Template":
            message = "Your Version has been saved in General Recipes."

        elif message == "Publish":
            message = "Your Version has been saved in Site Recipes"

        # Add Workflow State to Audit logs
        # AuditManagementAC.save_audit_entry(user_id, audit_message, input_json)
        response = {'status': "OK", 'message': "SUCCESS: " + message, "recipe_id": argument.get('recipe_id'),
                    "workspace_id": argument.get('workspace_id')}
        logger.info("#---------- Version Submitted Successfully ----------#")
        return response
    except Exception as e:
        raise Exception(str(e))


def list_solutions(solution_id):
    """
    lists solutions
    :param solution_id: Solution ID
    :return: List of all solutions
    """
    try:
        json_obj = canvas_instance_obj.get_list_solutions()
        res_js = {"content": {"solutions": []}}
        for item in json_obj:
            if item.get("id", "") != solution_id:
                res_js["content"]["solutions"].append(
                    {'id': item['id'], 'itemName': item["solutionName"], 'solutionID': item['solutionID']})
        return res_js
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def list_units_for_solution(solution_id):
    """
    This method is for fetching units based on solution id
    :param solution_id:
    :return:
    """
    try:
        response = canvas_instance_obj.get_solution_by_id(solution_id)
        unit_of_measure = []
        res_js = {"content": {"units": []}}
        if 'supportedUnit' in list(response.keys()):
            unit_of_measure = response.get('supportedUnit')
        elif response['solutionType'].lower() == 'solution':
            unit_of_measure = [response['componentUnit']]
        elif response['solutionType'].lower() == 'component':
            for item in response['componentComposition']:
                unit_of_measure.append(item['unit'])
        uom = canvas_instance_obj.fetch_multiple_uom_records(unit_of_measure)
        for item in uom:
            res_js["content"]["units"].append({'id': item['id'], 'itemName': item["UoM"]})
        return res_js
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def get_solution_detail(solution_id, unit_id):
    """
    This method is for fetching units based on solution id
    :param solution_id:
    :param unit_id
    :return:
    """
    try:
        response = canvas_instance_obj.get_solution_by_id(solution_id)
        res_js = {"content": {"solution": dict()}}
        res_js['content']['solution']['solution'] = solution_id
        res_js['content']['solution']['unit'] = unit_id
        if response['solutionType'].lower() == 'solution':
            if unit_id == response['componentUnit']:
                res_js['content']['solution']['cost_per_unit'] = response['componentCostPerUnit']
        elif response['solutionType'].lower() == 'component':
            for item in response['componentComposition']:
                if unit_id == item['unit']:
                    res_js['content']['solution']['cost_per_unit'] = item['cost_per_unit']
                    break
        return res_js
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def edit_solutions(solution_data):
    """
    This method fetches a particular solution detail based on solution 'id'
    :param solution_data: Contains necessary details to fetch a particular solution
    :return: Details of a particular solution
    """
    try:
        solution_id = solution_data.get("solution", "")
        solution_record = canvas_instance_obj.get_solution_by_id(solution_id)
        if solution_record.get("solutionType", "").lower() == "component":
            solution_record.pop("componentUnit", None)
            solution_record.pop("componentCostPerUnit", None)
        return solution_record
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def fetch_equipments_temp(input_json):
    """
    This method fetches equipments related to an equipment class and to a particular site
    :param input_json: Contains detailed information about equipment class and selected site
    :return: Equipments related to Equipment Classes and selected site
    """
    try:
        # Initialize
        equipment_details_list = []

        # Fetch Equipment Class list
        equipment_class_list = input_json.get("equipment_class", [])

        # Form Site details JSON for fetching site to equipments record
        site_details = {
            "Site": input_json.get("siteId", ""),
            "Building": input_json.get("buildingId", ""),
            "line": input_json.get("lineId", "")
        }

        # Fetch Equipment records using equipment class list
        equipment_records_based_on_eq_class = canvas_instance_obj.fetch_equipments_related_to_equipment_class(
            equipment_class_list)

        # Fetch Site to Equipments record using site details
        site_to_equipments_record = canvas_instance_obj.fetch_site_to_equipments_record(site_details)

        # Fetch equipment from site to equipments record
        equipments_list = site_to_equipments_record.get("equipments", [])

        # Fetch Equipment records based on equipments list
        equipment_records_based_on_site = canvas_instance_obj.fetch_multiple_equipment_records(equipments_list)

        # Iterate through each equipment record which are fetched based on equipment class
        for each_record in equipment_records_based_on_eq_class:

            # Form equipment detail JSON
            equipment_detail_json = {'equipment_name': each_record['equipment'], 'id': each_record["id"]}

            # Add equipment_detail_json to equipment details list if not available
            if equipment_detail_json not in equipment_details_list:
                equipment_details_list.append(equipment_detail_json)

        # Iterate through each equipment record which are fetched based on site
        for each_record in equipment_records_based_on_site:

            # Form equipment detail JSON
            equipment_detail_json = {'equipment_name': each_record['equipment'], 'id': each_record["id"]}

            # Add equipment_detail_json to equipment details list if not available
            if equipment_detail_json not in equipment_details_list:
                equipment_details_list.append(equipment_detail_json)
        return equipment_details_list
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def fetch_equipments_v2(input_json):
    """
    This method fetches equipments related to an equipment class and to a particular site
    :param input_json: Contains detailed information about equipment class and selected site
    :return: Equipments related to Equipment Classes and selected site
    """
    try:
        # Initialize
        equipment_details_list = []
        equipments_related_to_eq_class = []
        equipments_relates_to_site = []
        equipments_json = dict()
        equipments_list = list()

        # Fetch Equipment Class list
        equipment_class_list = input_json.get("equipment_class", [])

        complete_equipment_class_list = fetch_multiple_equipment_sub_class_details(equipment_class_list)

        # Form Site details JSON for fetching site to equipments record
        site_list = input_json.get("sites", [])

        # Fetch all equipment records
        complete_equipment_records = canvas_instance_obj.fetch_all_equipment_records()

        # Iterate through each record and form equipments JSON
        for each_record in complete_equipment_records:
            equipments_json[each_record["id"]] = each_record

        # Fetch Equipment records using equipment class list
        equipment_records_based_on_eq_class = canvas_instance_obj.fetch_equipments_related_to_equipment_class(
            complete_equipment_class_list)

        # Fetch Site to Equipments record using site details
        site_to_equipments_records = canvas_instance_obj.fetch_multiple_site_to_equipments_record_using_site(site_list)

        # Fetch equipment from site to equipments records
        for each_record in site_to_equipments_records:
            equipments_list += each_record.get("equipments", [])

        equipments_list = list(set(equipments_list))

        # Fetch Equipment records based on equipments list
        equipment_records_based_on_site = canvas_instance_obj.fetch_multiple_equipment_records(equipments_list)

        # Iterate through each equipment record which are fetched based on equipment class
        for each_record in equipment_records_based_on_eq_class:
            equipments_related_to_eq_class.append(each_record.get("id", ""))

        # Iterate through each equipment record which are fetched based on site
        for each_record in equipment_records_based_on_site:
            equipments_relates_to_site.append(each_record.get("id", ""))

        # Fetch intersection of equipments related to equipment class and equipment related to site
        final_equipments_list = list(set(equipments_related_to_eq_class) & set(equipments_relates_to_site))

        # Iterate through each equipment in final equipments list
        for each_equipment in final_equipments_list:
            # Form equipment detail JSON
            equipment_detail_json = {'equipment_name': equipments_json[each_equipment]['equipment'],
                                     'id': each_equipment}

            # Add equipment details JSON to equipment details list
            equipment_details_list.append(equipment_detail_json)

        return equipment_details_list
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def fetch_parent_equipment_class_id(equipment_class_id, equipment_class_list, equipment_class_temp_list=None):
    """
    This method fetches the Parent Equipment Class ID for a given equipment class
    :param equipment_class_id: Equipment class ID
    :param equipment_class_list: Equipment class list
    :param equipment_class_temp_list: Equipment class temporary list
    :return:
    """
    try:
        if equipment_class_temp_list is None:
            equipment_class_temp_list = []

        # Check if equipment class ID not in Equipment Class List and Equipment Class ID is not None and empty string
        if equipment_class_id not in equipment_class_list and equipment_class_id is not None and equipment_class_id:

            # Fetch Equipment class record
            equipment_class_record = canvas_instance_obj.fetch_equipment_class_record(equipment_class_id)

            # Fetch Equipment Class ID
            parent_equipment_class_id = equipment_class_record.get("equipment_class_name", "")

            # Recursive call
            fetch_parent_equipment_class_id(parent_equipment_class_id, equipment_class_list, equipment_class_temp_list)

        else:
            equipment_class_temp_list.append(equipment_class_id)
        return equipment_class_temp_list[0]
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def fetch_equipments(input_json):
    """
    This method fetches equipments related to an equipment class and to a particular site
    :param input_json: Contains detailed information about equipment class and selected site
    :return: Equipments related to Equipment Classes and selected site
    """
    try:
        # Initialize
        equipment_details_list = []
        equipment_related_to_eq_class = []
        equipment_json = dict()

        # Fetch Equipment Class list
        equipment_class_list = input_json.get("equipment_class", [])

        complete_equipment_class_list = fetch_multiple_equipment_sub_class_details(equipment_class_list)

        # Fetch Equipment records using equipment class list
        equipment_records_based_on_eq_class = canvas_instance_obj.fetch_equipments_related_to_equipment_class(
            complete_equipment_class_list)

        # Iterate through each equipment record which are fetched based on equipment class
        for each_record in equipment_records_based_on_eq_class:
            equipment_related_to_eq_class.append(each_record.get("id", ""))
            equipment_json[each_record["id"]] = each_record

        # Iterate through each equipment in final equipments list
        for each_equipment in equipment_related_to_eq_class:
            # Fetch Parent Equipment Class ID
            equipment_class_id = fetch_parent_equipment_class_id(
                equipment_json[each_equipment]['equipment_class_id'], equipment_class_list)
            parents = canvas_instance_obj.fetch_equipment_classes_for_each_equipment(each_equipment)
            # Form equipment detail JSON
            equipment_detail_json = {
                'equipment_name': equipment_json[each_equipment]['equipment'],
                'id': each_equipment, 'equipment_class_id': equipment_class_id,"parents":parents}

            # Add equipment details JSON to equipment details list
            equipment_details_list.append(equipment_detail_json)

        return equipment_details_list
    except Exception as e:
        logger.error((traceback.format_exc()))
        logger.error(str(e))
        raise Exception(str(e))


def fetch_equipment_sub_class(equipment_class_id):
    """
    This method fetches equipment sub classes related to an equipment class
    :param equipment_class_id: Equipment Class ID
    :return: Equipment Classes related to an Equipment Class ID
    """
    try:
        equipment_sub_class_detail_list = []
        equipment_class_records = canvas_instance_obj.fetch_equipment_sub_class_related_to_equipment_class(
            equipment_class_id)
        for item in equipment_class_records:
            equipment_sub_class_detail_list.append({'equipment_class_name': item['equipment_sub_class_name'],
                                                    'id': item["id"]})
        equipment_sub_class_detail_list = sorted(equipment_sub_class_detail_list,
                                                 key=lambda k: k.get('equipment_class_name', '').lower())
        return equipment_sub_class_detail_list
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def list_approvers(user_id):
    """
    This method fetches list of user details
    :param user_id: User ID of the user
    :return: Approvers Details
    """
    try:
        approvers_details = []
        user_records = canvas_instance_obj.fetch_users()
        for each_record in user_records:
            if each_record["user_id"] != user_id:
                approvers_details.append({"userId": each_record["user_id"], "id": each_record["id"],
                                          "emailId": each_record["email_id"]})
        return approvers_details
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def check_folder_exists(folder_data):
    """
    This method checks if the folder already exists or not
    :param folder_data: Folder Data
    :return: True if folder already exists else False
    """
    try:
        # Fetch recipe Type
        recipe_type = folder_data.get("recipeType", "")

        # If recipe type is version
        if recipe_type.lower() == "version":
            recipe_data = folder_data

        else:
            recipe_data = {
                "selectedFilePath": folder_data.get("folderPath", ""),
                "recipeType": folder_data.get("recipeType", "")
            }

        # Fetch recipe records
        recipe_records = canvas_instance_obj.fetch_folder_records(recipe_data)
        if len(recipe_records) > 0:
            return True
        else:
            return False
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def create_folder(folder_data, user_role_code_list):
    """
    This method is for creating a folder using an empty recipe
    :param folder_data: Folder Data
    :param user_role_code_list: User Role Code List
    :return:
    """
    try:
        # Fetch file path
        folder_path = folder_data.get("folderPath", "")

        # Fetch folder name
        folder_name = folder_path.split("/")[-1]

        # If Folder already exists in the particular designation, give a warning message
        if check_folder_exists(folder_data):
            message = str(AppConstants.CanvasConstants.already_contains_folder_named).format(folder_name=folder_name)
            response = error_obj.result_error_template(message=message, error_category="Warning")

        else:
            type_ = "add"
            recipe_or_folder_type = "folder"

            logger.debug(str(AppConstants.CanvasConstants.type_logger) + str(type_))
            logger.debug(str(AppConstants.CanvasConstants.recipe_or_folder_type_logger) + str(recipe_or_folder_type))

            # Fetch recipe ID
            recipe_id = canvas_instance_obj.generate_recipe_id()

            # Fetch recipe type
            recipe_type = folder_data.get("recipeType", "")

            # Fetch current date and time
            timestamp = str(datetime.utcnow()).split('.')[0]

            # Form Recipe JSON for creation of a folder
            recipe_json = {
                "processName": "",
                "recipeType": recipe_type,
                "userId": folder_data.get("userId", ""),
                "selectedWorkspaceType": folder_data.get("selectedWorkspaceType", ""),
                "selectedFilePath": folder_path,
                "processType": folder_data.get("processType", ""),
                "productFamilyId": "",
                "id": recipe_id,
                "modified_ts": timestamp
            }

            # If recipe type is not version add workflow state
            if recipe_type not in ["version", "shared"]:
                recipe_json["workflow_state"] = "Approved"

            elif recipe_type == "shared":
                recipe_json["folder"] = True
            # Insert recipe record
            canvas_instance_obj.insert_recipe_record(recipe_id, recipe_json)

            # Form recipe details JSON for frontend
            recipe_details = {"dateModified": timestamp, "updated_by": folder_data.get("userId", ""), "modality": "",
                              "itemName": folder_name}

            # Form message and response JSON
            message = "Successfully created the folder"
            file_explorer_input_json = {
                "userId": folder_data.get("userId", ""),
                "recipeType": folder_data.get("recipeType", ""),
                "selectedFilePath": folder_data.get("selectedFilePath", ""),
                "searchKey": folder_data.get("searchKey", ""),
                "searchField": folder_data.get("searchField", {})
            }
            hierarchy_details = fetch_all_recipes_temp3(file_explorer_input_json,
                                                        user_role_code_list)
            response = {"status": "OK", "message": message, "recipeDetails": recipe_details,
                        "hierarchyDetails": hierarchy_details}
        AuditManagementAC.save_audit_entry()
        return response
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def get_modality_details(recipe_id):
    """
    fetch modality name from recipe
    :param template_id:
    :return:
    """
    try:
        # get recipe record
        # workspace_data = canvas_instance_obj.fetch_latest_workspace_using_record_id(recipe_id)
        # recipe_id = workspace_data.get('recipeId')
        modality_id = canvas_instance_obj.fetch_recipe_data(recipe_id).get('productFamilyId',"")
        modality_info = ConfigurationManagementAC.fetch_modality_record(modality_id)
        return {"modalityName": modality_info.get('modalityName',""), "id": modality_info.get('id',"")}
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def get_latest_version_of_gr_for_sr(workspace_id, user_id):
    """
    check new version of GR is available for SR
    :param workspace_id:
    :return: updated_status, updated_workspace_id
    """
    try:
        workspace_data = canvas_instance_obj.fetch_recipe(workspace_id)
        recipe_id = workspace_data.get('recipeId')
        if not check_recipe_edit_access(recipe_id, user_id):
            return False, None
        if workspace_data.get('processType', '') == 'site' and workspace_data.get('selectedWorkspaceType') != 'Public':
            recipe_id = workspace_data.get('recipeId')
            latest_workspace_info = canvas_instance_obj.get_sorted_workspace_version(recipe_id)[0]
            if workspace_data.get('selectedWorkspaceType') == 'Shared':
                latest_workspace_info = canvas_instance_obj.get_sorted_shared_workspace_version(recipe_id)[0]
            if latest_workspace_info.get('id') != workspace_id:
                return False, None
            template_id = workspace_data.get('workspaceTemplateId')
            template_version = canvas_instance_obj.fetch_recipe(template_id).get('accessed_ts')
            gr_data = canvas_instance_obj.fetch_recipe(template_id)
            recipe_id = gr_data.get('recipeId')
            latest_version = canvas_instance_obj.get_latest_version(recipe_id)[0]
            if latest_version.get('recipeObj', {}).get('workFlowReviewObj', {}).get('reviewState', False):
                return False, None

            if template_version is not None and latest_version['accessed_ts'] > template_version:
                return True, latest_version['id']
            else:
                return False, template_id
        else:
            return False, None
    except Exception as ex:
        logger.error(traceback.format_exc())
        logger.error(str(ex))
        raise Exception(str(ex))


def save_and_reload_site_recipe(workspace_id, workspace_template_id, user_id):
    """
    :param workspace_id:
    :param workspace_template_id:
    :param user_id:
    :return:
    """
    try:
        workspace_data = canvas_instance_obj.fetch_recipe(workspace_id)
        gr_data = canvas_instance_obj.fetch_recipe(workspace_template_id)
        workspace_data = compare_activity_params_gr_to_sr(gr_data, workspace_data)
        recipe_data = copy.deepcopy(workspace_data)
        recipe_data['createdFrom'] = workspace_id
        recipe_data['type'] = "update"
        recipe_data.pop('id', None)
        message, workspace_id = canvas_instance_obj.add_recipe(recipe_data, propagation=True)
        RecentProcessAC.update_recent_process(workspace_id, user_id)
        return workspace_id
    except Exception as ex:
        logger.error(str(ex))
        raise Exception(str(ex))


def compare_activity_params_gr_to_sr_temp(gr_data, sr_data):
    """
    :param gr_data:
    :param sr_data:
    :return:
    """
    try:
        # Fetch Steps from General Recipe
        steps_in_gr = [step['step_id'] for step in gr_data['recipeObj']['defaultData'].get('unitops')]
        # Fetch Steps from Site Recipe
        steps_in_sr = [step['step_id'] for step in sr_data['recipeObj']['defaultData'].get('unitops')]
        # Get List of steps present in Both General and Site recipes
        common_steps = [step for step in steps_in_gr if step in steps_in_sr]
        import json
        for step in common_steps:
            logger.info("step--->" + step)
            for activity in sr_data['recipeObj'][step]['activityParams']:
                logger.info("activity--->" + activity)
                remove_list = []
                add_list = []
                if activity in list(gr_data['recipeObj'][step]['activityParams'].keys()):
                    logger.info("activity in Gr--->")
                    # changing GR parameter to SR if parameter type is general
                    logger.info(len(sr_data['recipeObj'][step]['activityParams'][activity].get('params', [])))
                    for param in sr_data['recipeObj'][step]['activityParams'][activity].get('params', []):
                        logger.info("sr param--->" + json.dumps(param))
                        for gr_param in gr_data['recipeObj'][step]['activityParams'][activity].get('params', []):
                            logger.info("gr param--->" + json.dumps(gr_param))
                            if param['id'] == gr_param['id'] and param.get('paramType') == 'general':
                                logger.info("param in both--->")
                                logger.info(param['id'])

                                remove_list.append(param)
                                add_list.append(gr_param)
                                break
                    logger.info(json.dumps(remove_list))
                    logger.info(json.dumps(add_list))
                    for item in remove_list:
                        sr_data['recipeObj'][step]['activityParams'][activity]['params'].remove(item)
                    for item in add_list:
                        sr_data['recipeObj'][step]['activityParams'][activity]['params'].append(item)
                    # materials propagation
                    sr_materials = sr_data['recipeObj'][step]['activityParams'][activity].get('materials', {}).get(
                        'materialTemplateTableMetaInfo', {})
                    gr_materials = gr_data['recipeObj'][step]['activityParams'][activity].get('materials', {}).get(
                        'materialTemplateTableMetaInfo', {})
                    if sr_materials.get('id') == gr_materials.get('id') and sr_materials.get('id') is not None:
                        materials = sr_materials.get('materialTemplateBodyData', [])
                        for sr_material in sr_materials.get('materialTemplateBodyData', []):
                            for gr_material in gr_materials.get('materialTemplateBodyData', []):
                                if sr_material['materialsRowID'] == gr_material['materialsRowID']:
                                    index = materials.index(sr_material)
                                    materials.remove(sr_material)
                                    materials.insert(index, gr_material)
                                    break
                        sr_materials['materialTemplateBodyData'] = materials
        # update Template info of SR
        # sr_data['workspaceTemplateVersion'] = gr_data['accessed_ts']
        sr_data['workspaceTemplateId'] = gr_data['id']
        return sr_data
    except Exception as ex:
        logger.error(str(ex))
        raise Exception(str(ex))


def compare_activity_params_gr_to_sr_temp_1(gr_data, sr_data):
    try:
        # Fetch Steps from General Recipe
        steps_in_gr = [step['step_id'] for step in gr_data['recipeObj']['defaultData'].get('unitops')]

        # Fetch Steps from Site Recipe
        steps_in_sr = [step['step_id'] for step in sr_data['recipeObj']['defaultData'].get('unitops')]

        # Get List of steps present in Both General and Site recipes
        common_steps = [step for step in steps_in_gr if step in steps_in_sr]

        # Iterate through the common steps
        for step in common_steps:

            # Iterate through the Site Recipe Activities
            for activity in sr_data['recipeObj'][step]['activityParams']:

                # Check if the Site Recipe Activity is Available in General Recipe and Propagate the changes
                if activity in list(gr_data['recipeObj'][step]['activityParams'].keys()):

                    # Initialize
                    gr_parameter_json = {}
                    sr_parameter_json = {}
                    gr_equipment_class_json = {}
                    sr_equipment_class_json = {}
                    gr_material_json = {}
                    sr_material_json = {}

                    # Parameter Propagation
                    # Iterate through each parameter in General Recipe and form General Recipe Parameter JSON
                    for gr_param in gr_data['recipeObj'][step]['activityParams'][activity].get('params', []):
                        gr_parameter_json[gr_param.get("id", "")] = gr_param

                    # Iterate through each parameter in Site Recipe and form Site Recipe Parameter JSON
                    for sr_param in sr_data['recipeObj'][step]['activityParams'][activity].get('params', []):
                        sr_parameter_json[sr_param.get("id", "")] = sr_param

                    # Iterate through each general parameter
                    for each_parameter in gr_parameter_json:

                        # Fetch General and Site Parameter
                        sr_parameter = sr_parameter_json.get(each_parameter, {})
                        gr_parameter = gr_parameter_json.get(each_parameter, {})

                        # If the General Recipe parameter is not available in Site Recipe Propagate it
                        if not sr_parameter:
                            sr_data['recipeObj'][step]['activityParams'][activity]['params'].append(gr_parameter)

                        # Propagate parameter if there is any changes in General Recipe Parameter
                        # And Check if the Parameter Type is same in both General and Site Recipe
                        # Replacement of Parameter
                        elif sr_parameter and gr_parameter.get("paramType", None) == sr_parameter.get("paramType", ""):
                            sr_data['recipeObj'][step]['activityParams'][activity]['params'].remove(sr_parameter)
                            sr_data['recipeObj'][step]['activityParams'][activity]['params'].append(gr_parameter)

                    # Equipment Class Propagation
                    # Iterate through each equipment class in General Recipe and form GR Equipment Class JSON
                    for gr_equipment_class in gr_data['recipeObj'][step]['activityParams'][activity].get(
                            'equipParams', []):
                        gr_equipment_class_json[gr_equipment_class.get("equipmentClassId", "")] = gr_equipment_class

                    # Iterate through each equipment class in Site Recipe and form SR Equipment Class JSON
                    for sr_equipment_class in sr_data['recipeObj'][step]['activityParams'][activity].get(
                            'equipParams', []):
                        sr_equipment_class_json[sr_equipment_class.get("equipmentClassId", "")] = sr_equipment_class

                    # Iterate through each general equipment class
                    for each_equipment_class in gr_equipment_class_json:

                        # Fetch General and Site Equipment Classes
                        sr_equipment_class = sr_equipment_class_json.get(each_equipment_class, {})
                        gr_equipment_class = gr_equipment_class_json.get(each_equipment_class, {})

                        # If the Equipment class is not available in Site Recipe Propagate it
                        if not sr_equipment_class:
                            sr_data['recipeObj'][step]['activityParams'][activity]['equipParams'].append(
                                gr_equipment_class)

                        # Propagate Equipment Class if there is any changes in General Recipe Equipment Class
                        # And Check if the Equipment Class Type is same in both General and Site Recipe
                        # Replacement of equipment class
                        elif sr_equipment_class and gr_equipment_class.get(
                                "eqClassType", None) == sr_equipment_class.get("eqClassType", ""):

                            sr_data['recipeObj'][step]['activityParams'][activity]['equipParams'].remove(
                                sr_equipment_class)

                            sr_data['recipeObj'][step]['activityParams'][activity]['equipParams'].append(
                                gr_equipment_class)

                    # Materials propagation
                    # Fetch General and Site Materials

                    if not sr_data['recipeObj'][step]['activityParams'][activity].get('materials', {}):
                        sr_data['recipeObj'][step]['activityParams'][activity]['materials'] = \
                            gr_data['recipeObj'][step]['activityParams'][activity].get('materials', {})

                    sr_materials = sr_data['recipeObj'][step]['activityParams'][activity].get('materials',
                                                                                              {}).get(
                        'materialTemplateTableMetaInfo', {})
                    gr_materials = gr_data['recipeObj'][step]['activityParams'][activity].get('materials',
                                                                                              {}).get(
                        'materialTemplateTableMetaInfo', {})

                    # Iterate through each Site Recipe Material and Form Site Recipe Material JSON
                    for sr_material in sr_materials.get('materialTemplateBodyData', []):
                        sr_material_json[sr_material.get("materialsRowID", "")] = sr_material

                    # Iterate through each General Recipe Material and Form General Recipe Material JSON
                    for gr_material in gr_materials.get("materialTemplateBodyData", []):
                        gr_material_json[gr_material.get("materialsRowID", "")] = gr_material

                    # Iterate through each general recipe materials
                    for each_material in gr_material_json:

                        # Fetch General and Site Recipe Material
                        gr_material = gr_material_json.get(each_material, {})
                        sr_material = sr_material_json.get(each_material, {})

                        # If the material is not available in Site Recipe Propagate it
                        if not sr_material:
                            sr_materials['materialTemplateBodyData'].append(gr_material)

                        # Else Replace the Site Recipe Material with the General Recipe material
                        else:
                            sr_materials["materialTemplateBodyData"].remove(sr_material)
                            sr_materials["materialTemplateBodyData"].append(gr_material)

        # Assign General Recipe ID to the Site Recipe Workspace Template
        sr_data['workspaceTemplateId'] = gr_data['id']
        return sr_data
    except Exception as e:
        logger.error((traceback.format_exc()))
        logger.error(str(e))
        raise Exception(str(e))


def compare_activity_params_gr_to_sr(gr_data, sr_data):
    try:
        sr_data["recipeObj"] = gr_data.get("recipeObj", {})

        # Assign General Recipe ID to the Site Recipe Workspace Template
        sr_data['workspaceTemplateId'] = gr_data.get("id", "")
        return sr_data
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def fetch_equipment_classes_hierarchy_details(equipment_class_list, complete_equip_class_list,
                                              equipment_class_hierarchy_json=None):
    try:
        # Initialize
        equipment_class_new_list = list()

        # Fetch equipment class records
        equipment_class_records = canvas_instance_obj.fetch_multiple_equipment_class_records(equipment_class_list)

        if equipment_class_hierarchy_json is None:
            equipment_class_hierarchy_json = {}

        # Iterate through each equipment class record
        for each_record in equipment_class_records:
            # Fetch parent equipment class ID
            parent_id = each_record.get("equipment_class_name", "")

            # Fetch equipment subclass class ID
            child_id = each_record.get("id", "")

            # Add subclasses to the complete equipment class list
            complete_equip_class_list.append(child_id)

            # Form Equipment class hierarchy JSON
            equipment_class_hierarchy_json[child_id] = parent_id

            # Add parent equipment class ID to new list
            equipment_class_new_list.append(parent_id)

        # Perform recursion until the equipment class new list reaches base class
        if equipment_class_new_list != [AppConstants.ServiceConstants.equipment_class_base_class_id] and len(
                equipment_class_new_list) >= 1:
            fetch_equipment_classes_hierarchy_details(equipment_class_new_list, complete_equip_class_list,
                                                      equipment_class_hierarchy_json)
        return equipment_class_hierarchy_json, complete_equip_class_list
    except Exception as e:
        logger.error(traceback.format_exc())
        logger.error(str(e))
        raise Exception(str(e))


def fetch_multiple_equipment_sub_class_details(equipment_class_list, complete_equipment_class_list=None):
    """
    This method fetches equipment sub classes related to an equipment class
    :return: Equipment Sub Class List
    """
    try:
        # Initialize
        new_equipment_class_list = list()

        # Fetch equipment class records
        equipment_class_records = canvas_instance_obj.fetch_multiple_equipment_sub_class_related_to_equipment_class(
            equipment_class_list)

        # If Complete equipment class list is None, make it to an empty list
        if complete_equipment_class_list is None:
            complete_equipment_class_list = []

        # Iterate through each equipment class record
        for each_record in equipment_class_records:
            # Fetch parent ID
            parent_id = each_record.get("id", "")
            new_equipment_class_list.append(parent_id)
            complete_equipment_class_list.append(parent_id)

        if bool(new_equipment_class_list):
            fetch_multiple_equipment_sub_class_details(new_equipment_class_list, complete_equipment_class_list)
        complete_equipment_class_list = list(set(complete_equipment_class_list + equipment_class_list))
        return complete_equipment_class_list
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def fetch_multiple_equipment_sub_classes_list_temp(equipment_class_id, equipment_sub_class_list=None):
    """
    This method fetches equipment sub classes related to an equipment class
    :return: Equipment Sub Class List
    """
    try:
        # Fetch equipment class records
        equipment_class_record = canvas_instance_obj.fetch_sub_class_equipment_record(
            equipment_class_id)

        # If Complete equipment class list is None, make it to an empty list
        if equipment_sub_class_list is None:
            equipment_sub_class_list = []

        if equipment_class_id != AppConstants.ServiceConstants. \
                equipment_class_base_class_id and equipment_class_id != "" and equipment_class_id is not None and \
                equipment_class_record:
            equipment_sub_class_list.append(equipment_class_record.get("id"))
            fetch_multiple_equipment_sub_classes_list_temp(equipment_class_record.get("id"), equipment_sub_class_list)

        return equipment_sub_class_list
    except Exception as e:
        logger.error((traceback.format_exc()))
        logger.error(str(e))
        raise Exception(str(e))


def fetch_multiple_equipment_sub_classes_list(equipment_class_list, equipment_sub_class_list=None):
    """
    This method fetches equipment sub classes related to an equipment class
    :return: Equipment Sub Class List
    """
    try:
        # Fetch equipment class records
        equipment_class_records = canvas_instance_obj.fetch_multiple_equipment_sub_class_records(
            equipment_class_list)

        # If Complete equipment class list is None, make it to an empty list
        if equipment_sub_class_list is None:
            equipment_sub_class_list = []

        new_equipment_class_list = []
        for each_record in equipment_class_records:
            if each_record.get("id",
                               "") != AppConstants.ServiceConstants.equipment_class_base_class_id and each_record.get(
                "id", "") != "" and each_record.get("id", "") is not None and each_record:
                equipment_sub_class_list.append(each_record.get("id", ""))
                new_equipment_class_list.append(each_record.get("id", ""))

        if new_equipment_class_list:
            fetch_multiple_equipment_sub_classes_list(new_equipment_class_list, equipment_sub_class_list)
        return equipment_sub_class_list
    except Exception as e:
        logger.error((traceback.format_exc()))
        logger.error(str(e))
        raise Exception(str(e))


def fetch_multiple_equipment_subclass_handler(equipment_class_details_list):
    try:
        equipment_class_list = list()
        for each_equipment_class in equipment_class_details_list:
            equipment_class_list.append(each_equipment_class.get("id", ""))
        equipment_class_list = list(set(equipment_class_list))
        complete_equipment_class_list = fetch_multiple_equipment_sub_class_details(equipment_class_list)
        return complete_equipment_class_list
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def recipe_refresh(workspace_data):
    """
    This method is for refreshing recipe data items like equipment parameters and equipment class parameters
    :param workspace_data: Workspace Data
    :return: Workspace data after updating equipment parameters and equipment class parameters
    """
    try:
        step_id = workspace_data.get("payload", {}).get("step_id", "")
        updated_timestamp = ""
        workspace_record = \
            canvas_instance_obj.fetch_record_object_from_workspace_record(
                workspace_data.get("payload", {}).get("id", ""))
        for each_step in workspace_record.get("recipeObj", {}).get("defaultData", {}).get("unitops", []):
            if each_step.get("step_id", "") == step_id:
                updated_timestamp = each_step.get("updated_ts", "")
        uuid_user_id = uuid_mngmnt.uuid_encode(workspace_data.get("payload", "").get("userId", ""))
        uuid_update_ts = uuid_mngmnt.uuid_encode(updated_timestamp)

        change_log_unitop_data = workspace_record.get("change_logs", {}).get(step_id, {}).get(
            uuid_user_id, {}).get(uuid_update_ts, {})
        old_step_data = change_log_unitop_data or workspace_record.get('recipeObj', {}).get(
            step_id, {})

        old_recipe_obj = copy.deepcopy(workspace_record.get("recipeObj", {}))

        workspace_record['recipeObj'][step_id] = merge_patch_differences(
            workspace_data.get("payload", {}).get('patch', []), old_step_data)

        # Initialize
        workspace_type = workspace_data.get("payload", {}).get('selectedWorkspaceType')
        user_id = workspace_data.get("payload", {}).get('userId')
        equipment_class_list = []
        equipment_list = []
        omitted_step_keys = ["defaultData", "processFlowImg"]
        omitted_summary_keys = ["solution_class_summary", "equipment_class_summary", "equipments_summary", "sampling"]

        # Fetch recipe object
        recipe_obj = workspace_record.get("recipeObj", {})
        # for shared recipe
        checked_out_step = ""
        if workspace_type.lower() == "shared":
            for step in recipe_obj.get('defaultData', {}).get('unitops', []):
                if step.get('checked_out') and step.get('checked_out_by') == user_id:
                    checked_out_step = step.get('id')
                    break
        if not checked_out_step:
            raise RecipeRefreshException("Please ensure to check out the step before refreshing equipment details")

        # Iterate through each step
        for each_step in recipe_obj:

            # Check if the step is not there in omitted keys
            if (each_step not in omitted_step_keys and workspace_type.lower() != "shared") or \
                    (workspace_type.lower() == "shared" and each_step == checked_out_step):
                # Fetch activity details
                activity_params = recipe_obj[each_step].get("activityParams", {})

                # Iterate through each component
                for each_component in activity_params:

                    # Check if the component is in omitted summary keys
                    if each_component not in omitted_summary_keys:

                        # Fetch activity data
                        activity_data = activity_params[each_component]

                        # Fetch equipment class data
                        equipment_class_data_list = activity_data.get("equipParams", [])

                        # Fetch equipment data
                        equipment_data_list = activity_data.get("equipmentParameters", {})

                        # Iterate through each equipment class and add it to equipment class list
                        for each_equipment_class in equipment_class_data_list:
                            equipment_class_list.append({"id": each_equipment_class.get("equipmentClassId", "")})

                        # Iterate through each equipment and add it to equipment list
                        for each_equipment in equipment_data_list:
                            equipment_list.append({"id": each_equipment.get("equipmentId", "")})

        # Fetch equipment class parameters for the equipment class list
        equipment_class_parameters_json = equipment_class_parameters_handler_v3(equipment_class_list)

        # Fetch equipment parameters for the equipments list
        equipment_parameters_json = equipment_parameters_handler(equipment_list)

        # Iterate through each step
        for each_step in recipe_obj:

            # Check if the step is not there in omitted step keys
            if (each_step not in omitted_step_keys and workspace_type.lower() != "shared") or \
                    (workspace_type.lower() == "shared" and each_step == checked_out_step):
                # Initialize
                equipment_class_dropdown_list = []

                # Fetch equipment class records
                equipment_class_records = canvas_instance_obj.get_list_equipment_class(each_step)

                # Iterate through each equipment class record and form equipment class dropdown list
                for each_record in equipment_class_records:
                    equipment_class_dropdown_list.append({"id": each_record.get("id", ""),
                                                          "itemName": each_record.get("equipment_sub_class_name", "")}
                                                         )

                # # Replace old equipment classes with a new one
                # recipe_obj[each_step]["equipmentClassParams"] = equipment_class_dropdown_list

                # Fetch activity details
                activity_params = recipe_obj[each_step].get("activityParams", {})

                # Iterate through each component
                for each_component in activity_params:

                    # Check if the step is not 'solution_class_summary' or 'equipments_summary'
                    if each_component not in ["solution_class_summary", "equipments_summary", "sampling"]:

                        # # Update equipment classes with a new one for each activity
                        # recipe_obj[each_step]["activityParams"][each_component]["data"] = equipment_class_dropdown_list

                        # Fetch activity data
                        activity_data = activity_params[each_component]

                        # Fetch equipment class data
                        equipment_class_data_list = activity_data.get("equipParams", [])

                        # Fetch equipment data
                        equipment_data_list = activity_data.get("equipmentParameters", [])

                        # Iterate through each equipment class
                        for each_equipment_class in equipment_class_data_list:
                            # Update equipment class parameters for the equipment class
                            each_equipment_class["params"] = equipment_class_parameters_json.get(
                                each_equipment_class.get("equipmentClassId", ""), []
                            )

                        # Iterate through each equipment in a line and add it to equipment list
                        for each_equipment in equipment_data_list:
                            # Update equipment parameters for the equipment
                            each_equipment["params"] = equipment_parameters_json.get(
                                each_equipment.get("equipmentId", ""), []
                            )
                    # If the component is equipments summary
                    elif each_component == "equipments_summary":

                        # Fetch equipment summary data
                        equipment_summary_data = activity_params[each_component]

                        # Fetch equipment data list
                        equipment_data_list = equipment_summary_data.get("equipments", [])

                        # Iterate through each equipment
                        for each_equipment in equipment_data_list:
                            # Fetch equipment ID
                            equipment_id = each_equipment.get("equipmentId", "")

                            # Update Equipment parameters in equipments summary
                            each_equipment["params"] = equipment_parameters_json.get(
                                equipment_id, []
                            )

        latest_patch = jsonpatch.make_patch(old_recipe_obj.get(step_id, ""), recipe_obj.get(step_id, ""))

        # Create Suggestion List for Recipe
        workspace_data["payload"]["recipeObj"] = recipe_obj
        CalculationBuilderAC.create_suggestion_list(workspace_data)
        # Fetch Updated Recipe Object for the workspace
        # updated_workspace_data = CalculationBuilderAC.recipe_comparison(workspace_data)
        # Add updated recipe object after calculations to workspace
        try:
            workspace_data["payload"]["patch"] = list(latest_patch)
            # # Save shared workspace will take step data from input json only if propagate_flag is true
            workspace_data["payload"]["propagation_flag"] = True
            CollaborationManagementAC.save_shared_workspace_unitop(workspace_data.get("payload", {}))
        except Exception as e:
            logger.error(str(e))
        # return workspace_data
        return "Refreshed Successfully."
    except RecipeRefreshException as ex:
        raise RecipeRefreshException(str(ex))
    except Exception as e:
        logger.error((traceback.format_exc()))
        logger.error(str(e))
        raise Exception(str(e))


def equipment_class_parameters_handler(equipment_class_values):
    """"
    This method is for fetching parameters for a particular equipment_class
    :param equipment_class_values: Equipment Class
    :return: Parameters linked with a particular equipment_class
    """
    try:
        # Initialize
        response = dict()
        equipment_class_list = []
        complete_equip_class_list = []
        equipment_parameter_list = []
        equipment_parameter_template_list = []
        uom_list = []
        mapped_parameters_list = []
        equipment_parameter_json = dict()
        equipment_class_hierarchy_json = dict()
        equipment_parameter_template_json = dict()
        uom_json = dict()

        # Iterate through each equipment class and append to a list
        for each_equipment_class in equipment_class_values:
            equipment_class_list.append(each_equipment_class.get("id", ""))

        # Remove duplicates
        equipment_class_list = list(set(equipment_class_list))

        # Fetch equipment class hierarchy JSON and complete equipment class list including parent classes
        equipment_class_hierarchy_json, complete_equip_class_list = fetch_equipment_classes_hierarchy_details(
            equipment_class_list,
            complete_equip_class_list,
            equipment_class_hierarchy_json)

        # Fetch equipment class parameter records
        equipment_class_parameter_records = canvas_instance_obj.fetch_multiple_equipment_class_parameter_records(
            complete_equip_class_list
        )

        # Iterate through equipment class parameter record
        for each_equipment_class_record in equipment_class_parameter_records:

            # Fetch mapped parameters
            mapped_parameters = each_equipment_class_record.get("mappedParameters", [])

            # Iterate through each parameter details and fetch equipment parameter and equipment parameter template
            for each_parameter_record in mapped_parameters:
                mapped_parameters_list.append(each_parameter_record)
                equipment_parameter_template_list.append(each_parameter_record.get("template", ""))
                equipment_parameter_list.append(each_parameter_record.get("parameterId", ""))

        # Remove duplicates
        equipment_parameter_template_list = list(set(equipment_parameter_template_list))
        equipment_parameter_list = list(set(equipment_parameter_list))

        # Fetch Equipment Parameter records
        equipment_parameter_records = canvas_instance_obj.fetch_multiple_equipment_parameter_definitions(
            equipment_parameter_list)

        # Fetch Equipment Parameter Template records
        equipment_parameter_template_records = canvas_instance_obj. \
            fetch_multiple_equipment_parameter_template_records(equipment_parameter_template_list)

        # Iterate through each equipment parameter records
        for each_record in equipment_parameter_records:
            # Form Equipment Parameter JSON
            equipment_parameter_json[each_record["id"]] = each_record

            # Form UoM list
            uom_list.append(each_record.get("uom", ""))

        # Fetch measure records
        uom_records = canvas_instance_obj.fetch_multiple_uom_records(uom_list)

        # Form Unit of Measure JSON
        for each_record in uom_records:
            uom_json[each_record["id"]] = each_record.get("UoM", "")

        # Form Parameter Template JSON
        for each_record in equipment_parameter_template_records:
            equipment_parameter_template_json[each_record["id"]] = each_record

        # Iterate through each equipment class parameter record
        for each_equipment_class_record in equipment_class_parameter_records:

            # Fetch mapped parameters
            mapped_parameters = each_equipment_class_record.get("mappedParameters", [])

            # Fetch equipment class ID
            equipment_class_id = each_equipment_class_record.get("equipment", "")

            # Iterate through each parameter
            for each_parameter in mapped_parameters:

                # Fetch Values
                values = each_parameter.get("values", [])

                # Fetch Fields
                fields = equipment_parameter_template_json[each_parameter["template"]]["fields"]

                # Fetch facility fit rules for equipment parameters from equipment parameter template
                try:
                    facility_fit_rules = equipment_parameter_template_json[each_parameter["template"]][
                        "facility_fit_configuration"]
                except Exception as e:
                    logger.error(str(e))
                    facility_fit_rules = {}

                # calculation formula
                calculation_formula = equipment_parameter_json[each_parameter.get("parameterId", "")].get(
                    'field_formula_list', {})

                # Mapping parameter template fields with equipment class parameter values
                field_value_list = CommonAC.merge_template_equip_class_params(fields, values, calculation_formula)

                # Map fields and values for parameters
                value = field_parameter_mapping(field_value_list)

                # Fetch parameter data
                parameter_id = each_parameter.get("parameterId", "")
                parameter_data = equipment_parameter_json.get(parameter_id, {})

                # Fetch UoM ID
                uom_id = parameter_data.get("uom", "")

                # Fetch Parameter Name
                param_label = parameter_data.get("parameterName", "")
                # Remove spaces in parameter key
                param_key = param_label.replace(" ", "_").lower()

                # Fetch UoM Name
                uom = uom_json.get(uom_id, "")

                if equipment_class_id not in list(response.keys()):
                    response[equipment_class_id] = []

                # Final Response
                response[equipment_class_id].append({
                    "param_key": param_key,
                    "param_label": param_label,
                    "fields": field_value_list,
                    "value": value,
                    "id": parameter_id,
                    "uom": uom,
                    "facility_fit_configuration": facility_fit_rules
                })
        return response
    except Exception as e:
        logger.error((traceback.format_exc()))
        logger.error(str(e))
        raise Exception(str(e))


def fetch_equipment_classes_parent_child_hierarchy_details(equipment_class_list):
    """
    This method fetches base equipment class to equipment sub class relationship JSON
    :param equipment_class_list: Equipment Class list for which sub class relations are required
    :return: Equipment CLass to Equipment Sub Class Relationship JSON
    """
    try:
        # Initialize
        complete_equipment_class_list = []

        # Fetch equipment base class to equipment sub class records
        equipment_base_class_to_equipment_sub_class_records = \
            canvas_instance_obj.fetch_multiple_sub_class_related_to_base_class(equipment_class_list)

        for each_equipment_class in equipment_class_list:
            count = 0
            for each_record in equipment_base_class_to_equipment_sub_class_records:
                if each_record.get("_id") == each_equipment_class:
                    count += 1
            if count == 0:
                equipment_base_class_to_equipment_sub_class_records.append({"_id": each_equipment_class,
                                                                            "subClasses": []})

        # Iterate through each equipment base class to equipment sub class records
        for each_record in equipment_base_class_to_equipment_sub_class_records:

            # Append Base Class ID to Complete Equipment Class list
            complete_equipment_class_list.append(each_record.get("_id"))

            # Iterate through each equipment sub class
            for each_equipment_sub_class in each_record.get("subClasses", []):
                # Append each equipment sub class to complete equipment class list
                complete_equipment_class_list.append(each_equipment_sub_class)

        return equipment_base_class_to_equipment_sub_class_records, complete_equipment_class_list
    except Exception as e:
        logger.error((traceback.format_exc()))
        logger.error(str(e))
        raise Exception(str(e))


def equipment_class_parameters_handler_v2(equipment_class_values):
    try:
        # Initialize
        response = dict()
        equipment_class_list = []
        equipment_parameter_list = []
        equipment_parameter_template_list = []
        uom_list = []
        mapped_parameters_list = []
        equipment_parameter_json = dict()
        equipment_parameter_template_json = dict()
        equipment_class_parameters_json = dict()
        uom_json = dict()

        # Iterate through each equipment class and append to a list
        for each_equipment_class in equipment_class_values:
            equipment_class_list.append(each_equipment_class.get("id", ""))

        # Remove duplicates
        equipment_class_list = list(set(equipment_class_list))

        # Fetch equipment class hierarchy JSON and complete equipment class list including parent classes
        equipment_base_class_to_equipment_sub_class_records, complete_equip_class_list = \
            fetch_equipment_classes_parent_child_hierarchy_details(equipment_class_list)

        # Fetch equipment class parameter records
        equipment_class_parameter_records = canvas_instance_obj.fetch_multiple_equipment_class_parameter_records(
            complete_equip_class_list
        )

        # Iterate through equipment class parameter record
        for each_equipment_class_parameter_record in equipment_class_parameter_records:

            # Fetch mapped parameters
            mapped_parameters = each_equipment_class_parameter_record.get("mappedParameters", [])

            equipment_class_parameters_json[each_equipment_class_parameter_record["equipment"]] = \
                each_equipment_class_parameter_record

            # Iterate through each parameter details and fetch equipment parameter and equipment parameter template
            for each_parameter_record in mapped_parameters:
                mapped_parameters_list.append(each_parameter_record)
                equipment_parameter_template_list.append(each_parameter_record.get("template", ""))
                equipment_parameter_list.append(each_parameter_record.get("parameterId", ""))

        # Remove duplicates
        equipment_parameter_template_list = list(set(equipment_parameter_template_list))
        equipment_parameter_list = list(set(equipment_parameter_list))

        # Fetch Equipment Parameter records
        equipment_parameter_records = canvas_instance_obj.fetch_multiple_equipment_parameter_definitions(
            equipment_parameter_list)

        # Fetch Equipment Parameter Template records
        equipment_parameter_template_records = canvas_instance_obj. \
            fetch_multiple_equipment_parameter_template_records(equipment_parameter_template_list)

        # Iterate through each equipment parameter records
        for each_record in equipment_parameter_records:
            # Form Equipment Parameter JSON
            equipment_parameter_json[each_record["id"]] = each_record

            # Form UoM list
            uom_list.append(each_record.get("uom", ""))

        # Fetch measure records
        uom_records = canvas_instance_obj.fetch_multiple_uom_records(uom_list)

        # Form Unit of Measure JSON
        for each_record in uom_records:
            uom_json[each_record["id"]] = each_record.get("UoM", "")

        # Form Parameter Template JSON
        for each_record in equipment_parameter_template_records:
            equipment_parameter_template_json[each_record["id"]] = each_record

        # Iterate through each equipment base class to equipment sub class records
        for each_record in equipment_base_class_to_equipment_sub_class_records:

            # Fetch Equipment Class ID
            equipment_class_id = each_record.get("_id", "")

            # Fetch Equipment Sub Class List
            sub_class_list = each_record.get("subClasses", [])

            # Add Base Class ID at front
            # Base Class Parameters have to be added first
            sub_class_list.insert(0, equipment_class_id)

            for each_equipment_class in sub_class_list:

                # Fetch mapped parameters
                mapped_parameters = equipment_class_parameters_json.get(each_equipment_class, {}).get(
                    "mappedParameters", [])

                # Iterate through each parameter
                for each_parameter in mapped_parameters:

                    # Fetch Values
                    values = each_parameter.get("values", [])

                    # Fetch Fields
                    fields = equipment_parameter_template_json.get(each_parameter.get("template", ""), {}).get(
                        "fields", [])

                    # Fetch facility fit rules for equipment parameters from equipment parameter template
                    try:
                        facility_fit_rules = equipment_parameter_template_json[each_parameter["template"]][
                            "facility_fit_configuration"]
                    except Exception as e:
                        logger.error(str(e))
                        facility_fit_rules = {}

                    # calculation formula
                    calculation_formula = equipment_parameter_json[each_parameter.get("parameterId", "")].get(
                        'field_formula_list', {})

                    # Mapping parameter template fields with equipment class parameter values
                    field_value_list = CommonAC.merge_template_equip_class_params(fields, values, calculation_formula)

                    # Map fields and values for parameters
                    value = field_parameter_mapping(field_value_list)

                    # Fetch parameter data
                    parameter_id = each_parameter.get("parameterId", "")
                    parameter_data = equipment_parameter_json.get(parameter_id, {})

                    # Fetch UoM ID
                    uom_id = parameter_data.get("uom", "")

                    # Fetch Parameter Name
                    param_label = parameter_data.get("parameterName", "")
                    # Remove spaces in parameter key
                    param_key = param_label.replace(" ", "_").lower()

                    # Fetch UoM Name
                    uom = uom_json.get(uom_id, "")

                    if equipment_class_id not in list(response.keys()):
                        response[equipment_class_id] = []

                    # Form Params JSON
                    params_json = {
                        "param_key": param_key,
                        "param_label": param_label,
                        "fields": field_value_list,
                        "value": value,
                        "id": parameter_id,
                        "uom": uom,
                        "facility_fit_configuration": facility_fit_rules
                    }

                    # Final Response
                    # If Parameter is not added already then add it to the response
                    if not any(param['id'] == parameter_id for param in response[equipment_class_id]):
                        response[equipment_class_id].append(params_json)

        return response
    except Exception as e:
        logger.error((traceback.format_exc()))
        logger.error(str(e))
        raise Exception(str(e))


def equipment_class_parameters_handler_v3(equipment_class_values):
    """
    This method fetches the equipment classes and their corresponding parameters including parent class parameters
    :param equipment_class_values: Equipment Class List
    :return: Equipment Classes and their corresponding parameters
    """
    try:
        # Initialize
        response = dict()
        equipment_class_list = []
        equipment_parameter_list = []
        equipment_parameter_template_list = []
        uom_list = []
        equipment_parameter_json = dict()
        equipment_parameter_template_json = dict()
        equipment_class_to_equipment_class_parameters_json = dict()
        uom_json = dict()

        # Iterate through each equipment class and append to a list
        for each_equipment_class in equipment_class_values:
            equipment_class_list.append(each_equipment_class.get("id", ""))

        # Remove duplicates
        equipment_class_list = list(set(equipment_class_list))
        # Iterate through each equipment class
        for each_equipment_class in equipment_class_list:

            # Fetch Equipment parent class equipment class and the corresponding equipment class parameters
            equipment_class_parameters_dict = ConfigurationManagementAC.get_equipment_class_parameters(
                each_equipment_class)
            # Assign Equipment Class Parameters to the corresponding equipment class
            equipment_class_to_equipment_class_parameters_json[each_equipment_class] = equipment_class_parameters_dict

            # Iterate through each equipment class parameter
            for parameter, parameter_data in list(equipment_class_parameters_dict.items()):
                # Add Equipment Class Parameter to the equipment parameter
                equipment_parameter_list.append(parameter)
                equipment_parameter_template_list.append(parameter_data.get("template", ""))

        # Fetch Equipment Parameter records
        equipment_parameter_records = canvas_instance_obj.fetch_multiple_equipment_parameter_definitions(
            equipment_parameter_list)

        # Fetch Equipment Parameter Template records
        equipment_parameter_template_records = canvas_instance_obj. \
            fetch_multiple_equipment_parameter_template_records(equipment_parameter_template_list)

        # Iterate through each equipment parameter records
        for each_record in equipment_parameter_records:
            # Form Equipment Parameter JSON
            equipment_parameter_json[each_record["id"]] = each_record

            # Form UoM list
            uom_list.append(each_record.get("uom", ""))

        # Fetch measure records
        uom_records = canvas_instance_obj.fetch_multiple_uom_records(uom_list)

        # Form Unit of Measure JSON
        for each_record in uom_records:
            uom_json[each_record["id"]] = each_record.get("UoM", "")

        # Form Parameter Template JSON
        field_id_list = []
        for each_record in equipment_parameter_template_records:
            equipment_parameter_template_json[each_record["id"]] = each_record
            for each_field in each_record.get('fields', []):
                field_id_list.append(each_field.get('fieldId', ''))

        parameter_attributes_json = {}
        parameter_attribute_records = canvas_instance_obj.fetch_multiple_parameter_attribute_records(
            list(set(field_id_list)))
        for each_record in parameter_attribute_records:
            parameter_attributes_json[each_record.get("id", "")] = each_record

        # Iterate through each equipment class
        for equipment_class, equipment_class_parameter in equipment_class_to_equipment_class_parameters_json.items():

            # Iterate through each equipment class parameter
            for parameter, parameter_data in equipment_class_parameter.items():

                # Fetch Values
                values = parameter_data.get("values", [])

                # Fetch Fields
                fields = equipment_parameter_template_json[parameter_data.get("template", "")]["fields"]

                hidden_fields = equipment_parameter_template_json.get(parameter_data.get("template", ""), {}).get(
                    "pkm__h_fields", [])

                for each_field in fields:
                    each_field.update(parameter_attributes_json.get(each_field.get('fieldId', ''), {}))

                # calculation formula
                calculation_formula = equipment_parameter_json[parameter].get('field_formula_list', {})

                # Fetch facility fit rules for equipment parameters from equipment parameter template
                try:
                    facility_fit_rules = equipment_parameter_template_json[parameter_data["template"]][
                        "facility_fit_configuration"]
                except Exception as e:
                    logger.error(str(e))
                    facility_fit_rules = {}

                # Mapping parameter template fields with equipment class parameter values
                field_value_list = CommonAC.merge_template_equip_class_params(
                    fields, values, calculation_formula,
                    hidden_fields=hidden_fields,
                    parameter_attributes_json=parameter_attributes_json)
                
                # Map fields and values for parameters
                value = field_parameter_mapping(field_value_list)

                # Fetch parameter data
                parameter_data = equipment_parameter_json.get(parameter, {})

                # Fetch UoM ID
                uom_id = parameter_data.get("uom", "")

                # Fetch Parameter Name
                param_label = parameter_data.get("parameterName", "")

                # Remove spaces in parameter key
                param_key = param_label.replace(" ", "_").lower()

                # Fetch UoM Name
                uom = uom_json.get(uom_id, "")

                if equipment_class not in list(response.keys()):
                    response[equipment_class] = []

                # Form Params JSON
                params_json = {
                    "param_key": param_key,
                    "param_label": param_label,
                    "fields": field_value_list,
                    "value": value,
                    "id": parameter,
                    "uom": uom,
                    "facility_fit_configuration": facility_fit_rules
                }
                if not any(param['id'] == parameter for param in response[equipment_class]):
                    response[equipment_class].append(params_json)
        return response
    except Exception as e:
        logger.error((traceback.format_exc()))
        logger.error(str(e))
        raise Exception(str(e))


def fetch_equipment_sub_class_hierarchy_details(equipment_class_details_json, equipment_class_list,
                                                equipment_class_hierarchy_list,
                                                complete_equipment_class_list=None):
    try:
        # Initialize
        new_equipment_class_list = list()
        equipment_class_json = dict()

        # Fetch equipment class records
        equipment_class_records = canvas_instance_obj.fetch_multiple_equipment_sub_class_related_to_equipment_class(
            equipment_class_list)

        # If Complete equipment class list is None, make it to an empty list
        if complete_equipment_class_list is None:
            complete_equipment_class_list = []

        if equipment_class_hierarchy_list is None:
            equipment_class_hierarchy_list = []

        for each_record in equipment_class_records:
            equipment_class_json[each_record.get("id", "")] = each_record

        # Iterate through each equipment class record
        for each_record in equipment_class_records:
            # Fetch parent ID
            parent_id = each_record.get("id", "")
            new_equipment_class_list.append(parent_id)
            complete_equipment_class_list.append(parent_id)
            equipment_class_hierarchy_json = {
                "parent_class": each_record.get("equipment_class_name", ""),
                "id": each_record.get("id", ""),
                "parent_class_name": equipment_class_details_json.get(each_record.get(
                    "equipment_class_name", ""), {}).get("equipment_sub_class_name", ""),
                "class_name": each_record.get("equipment_sub_class_name", "")}
            if equipment_class_hierarchy_json not in equipment_class_hierarchy_list:
                equipment_class_hierarchy_list.append(equipment_class_hierarchy_json)

        if bool(new_equipment_class_list):
            fetch_equipment_sub_class_hierarchy_details(equipment_class_details_json,
                                                        new_equipment_class_list, equipment_class_hierarchy_list,
                                                        complete_equipment_class_list)
        complete_equipment_class_list = list(set(complete_equipment_class_list + equipment_class_list))
        return complete_equipment_class_list, equipment_class_hierarchy_list
    except Exception as e:
        logger.error((traceback.format_exc()))
        logger.error(str(e))
        raise Exception(str(e))


def form_equipment_class_hierarchy(equipment_class_hierarchy_list, equipment_class_root_json):
    try:
        # Initialize
        nodes = {}
        converted_equipment_class_hierarchy_list = []

        # Iterate through each equipment class hierarchy list
        for each_equipment_class in equipment_class_hierarchy_list:
            equipment_class_id = each_equipment_class.get("id", "")
            parent_class_name = each_equipment_class.get("class_name", "")
            nodes[equipment_class_id] = {
                "parent_class_id": equipment_class_id,
                "parent_class": parent_class_name,
                "children": []
            }

        # Add equipment Class Root JSON with Nodes
        nodes.update(equipment_class_root_json)

        # pass 2: create trees and parent-child relations
        forest = []
        for each_equipment_class in equipment_class_hierarchy_list:
            equipment_class_id = each_equipment_class.get("id", "")
            parent_id = each_equipment_class.get("parent_class", "")
            node = nodes[equipment_class_id]

            # either make the node a new tree or link it to its parent
            if equipment_class_id == parent_id:
                # start a new tree in the forest
                forest.append(node)
            else:
                # add new_node as child to parent
                parent = nodes.get(parent_id, {})
                if 'children' not in parent:
                    # ensure parent has a 'children' field
                    parent['children'] = []

                children = parent['children']
                children.append(node)
        for each_key in equipment_class_root_json:
            converted_equipment_class_hierarchy_list.append(nodes.get(each_key, {}))
        return converted_equipment_class_hierarchy_list
    except Exception as e:
        logger.error((traceback.format_exc()))
        logger.error(str(e))
        raise Exception(str(e))


def fetch_all_equipment_sub_class(step_id, template):
    """
    This method fetches all equipment sub classes linked to a equipment class
    :param step_id: Step ID
    :param template: Template Type
    :return: Equipment Class Hierarchy Data. Equipment sub classes linked to a equipment class
    """
    try:
        # Initialize
        equipment_class_list = []
        equipment_class_root_json = {}
        equipment_class_details_json = {}
        complete_equip_class_list = []
        equipment_class_hierarchy_list = []

        # Fetch all Equipment class records
        equipment_class_records = canvas_instance_obj.fetch_all_equipment_class_records()

        # Fetch Step to equipment class record based on Step ID
        if template.lower() == "activity_template":
            # Iterate through all equipment class record and form equipment class details JSON
            for each_record in equipment_class_records:
                if each_record.get(
                        "equipment_class_name") == AppConstants.ServiceConstants.equipment_class_base_class_id:
                    equipment_class_details_json[each_record.get("id", "")] = each_record
                    equipment_class_list.append(each_record.get("id"))

        else:

            step_to_equipment_class_record = canvas_instance_obj.fetch_step_to_equipment_class_record(step_id)

            # Fetch equipment class list from the step to equipment class record
            equipment_class_list = step_to_equipment_class_record.get("equipment_class", [])

            for each_record in equipment_class_records:
                equipment_class_details_json[each_record.get("id", "")] = each_record

        # Iterate through the required equipment classes and form equipment class root JSON for Hierarchy Structure
        for each_equipment_class in equipment_class_list:
            equipment_class_root_json[each_equipment_class] = {
                "parent_class_id": each_equipment_class,
                "parent_class": equipment_class_details_json[each_equipment_class]["equipment_sub_class_name"],
                "children": []}

        # Fetch equipment class hierarchy JSON and complete equipment class list including parent classes
        complete_equip_class_list, equipment_class_hierarchy_list = fetch_equipment_sub_class_hierarchy_details(
            equipment_class_details_json,
            equipment_class_list,
            equipment_class_hierarchy_list,
            complete_equip_class_list)

        # Fetch Hierarchy JSON based on the User Interface Requirement
        final_equipment_class_hierarchy_json = form_equipment_class_hierarchy(equipment_class_hierarchy_list,
                                                                              equipment_class_root_json)

        # Form Response
        response = {"equipmentClass": final_equipment_class_hierarchy_json}
        return response
    except Exception as e:
        logger.error((traceback.format_exc()))
        logger.error(str(e))
        raise Exception(str(e))


def publish_to_shared_workspace(recipe_data):
    """
    create shared workspace implementation
    :param recipe_data: Recipe JSON
    :return:
    """
    try:
        type_ = "add"
        creation_type = "publish_shared"
        recipe_or_folder_type = "recipe"
        logger.debug(str(AppConstants.CanvasConstants.type_logger) + str(type_))
        logger.debug(str(AppConstants.CanvasConstants.recipe_or_folder_type_logger) + str(recipe_or_folder_type))
        input_json = recipe_data["payload"]
        workspace_record = \
            canvas_instance_obj.fetch_record_object_from_workspace_record(
                input_json.get('id', ''))

        old_step_data = workspace_record.get('recipeObj', {}).get(input_json.get("step_id", ""), {})
        workspace_record['recipeObj'][input_json.get("step_id", "")] = merge_patch_differences(
            input_json.get('patch', []), old_step_data)

        input_json['recipeObj'] = workspace_record.get('recipeObj', {})
        input_json['processName'] = input_json.get('processName', "").strip()
        user_id = input_json['userId']
        file_path = input_json['selectedFilePath']
        data = remove_comment_status_from_recipe(input_json['recipeObj'])
        recipe_type = "shared"
        modality_id = input_json.get('modalityId', '')
        process_name = input_json.get('processName', '')
        recipe_id = input_json.get('recipeId', '')
        phase_details = input_json.get("phaseDetails",{})
        material_quantity_metadata = input_json.get("material_quantity_metadata",{})
        description = input_json.get("recipeDescription","")
        process_type = input_json.get('processType', 'general')
        selected_workspace_type = input_json.get('selectedWorkspaceType', 'shared')
        workspace_template_version = input_json.get("workspaceTemplateVersion", "NA")
        workspace_template_id = input_json.get("workspaceTemplateId", "NA")
        workspace_template_name = input_json.get("workspaceTemplateName", "NA")
        submission_ts = str(datetime.utcnow()).split('.')[0]
        created_from = input_json.get('createdFrom', "")
        viewer_status = input_json.get("viewer_status", "")
        # material validation
        material_id = input_json.get("materialID", "")
        material_name = input_json.get("material_name", "")
        material_row_id = input_json.get("materialRowId", "")
        recipe_objective = input_json.get("Objective", "")
        product_scale = input_json.get("productScale", "")
        input_json['processName'] = process_name

        product_family_name = canvas_instance_obj.fetch_modality_record(modality_id).get('modalityName')

        user_groups = canvas_instance_obj.update_users_of_workflow(input_json.get('recipeEditors', []),
                                                                   input_json.get('recipeOwners', []),
                                                                   input_json.get('recipeViewers', []),
                                                                   user_id)
        
        sites = input_json.get('sites', [])
        query = {}

        for item in data.get('defaultData', {}).get('unitops', []):
            item.pop('checked_out', None)
            item.pop('checked_out_by', None)
            item.pop('requestButtonAccess', None)
            item.pop('versionInfo', None)
            item.pop('status', None)
            item.pop('version', None)
            item.pop('reviewState', None)

        argument = {
            "user_id": user_id,
            "file_path": file_path,
            "process_name": process_name,
            "process_type": process_type,
            "recipeId": recipe_id,
            "phaseDetails": phase_details,
            "material_quantity_metadata":material_quantity_metadata,
            "recipeDescription":description,
            "data": data,
            "viewer_status": viewer_status,
            "recipe_type": recipe_type,
            "modality_id": modality_id,
            "productFamilyName": product_family_name,
            "submission_ts": submission_ts,
            "selected_workspace_type": selected_workspace_type,
            "query": query,
            "workspaceTemplateName": workspace_template_name,
            "workspaceTemplateId": workspace_template_id,
            "workspaceTemplateVersion": workspace_template_version,
            "createdFrom": created_from,
            "userGroups": user_groups,
            "sites": sites,
            "workflow_state": AppConstants.RecipeStates.DRAFT_STATE,
            "version_label": AppConstants.shared_recipe_version_format.format(major_version=str(0),
                                                                              version=1,
                                                                              minor_version=0),
            "materialID": material_id,
            "material_name": material_name,
            "materialRowId": material_row_id,
            "recipeDecorators": input_json.get("recipeDecorators", {})
        }
        if process_type == "experimental":
            recipe_record = canvas_instance_obj.fetch_recipe_record(recipe_id)
            argument["Objective"] = recipe_objective
            argument["productScale"] = product_scale
            argument["linkedRecipe"] = input_json.get("linkedRecipe", [])
            argument["run_template_id"] = recipe_record.get('run_template_id', '')
            argument['experimentalConstruct'] = input_json.get('experimentalConstruct', {})

        # Check if a recipe already exists with same name in a specific location
        if canvas_instance_obj.check_recipe_exists(input_json):
            warning_message = str(AppConstants.CanvasConstants.recipe_already_exists).format(process_name, "ps")
            response = error_obj.result_error_template(message=warning_message, error_category="Warning")
            return response
        elif canvas_instance_obj.check_material_exists(material_id):
            warning_message = str(AppConstants.CanvasConstants.material_already_associated).format(process_name, "ps")
            response = error_obj.result_error_template(message=warning_message, error_category="Warning")
            return response
        else:
            recipe_id, workspace_id, workflow_template_id = canvas_instance_obj.publish_to_shared(argument)
            workflow_instance_id = WorkflowManagerAC.create_workflow(process_name, recipe_id, workflow_template_id)
            canvas_instance_obj.add_workflow_instance_to_project(recipe_id, workflow_instance_id)
            material_to_recipe_relation_obj = {"materialID": material_id, "recipeId": recipe_id,
                                               "material_name": material_name, "materialRowId": material_row_id}
            if input_json.get("recipeType", "").lower() == 'shared':
                canvas_instance_obj.add_material_to_recipe_relation(material_to_recipe_relation_obj)
                if material_row_id:
                    canvas_instance_obj.add_recipe_details_to_materials(material_row_id, recipe_id,
                                                                        input_json, process_name)

        message = "Your Version has been saved to shared Recipes."

        # Add Workflow State to Audit logs
        AuditManagementAC.save_audit_entry()
        response = {'status': "OK", 'message': "SUCCESS: " + message}
        logger.info("#---------- Version Submitted Successfully ----------#")
        return response
    except Exception as e:
        logger.error((traceback.format_exc()))
        logger.error(str(e))
        raise Exception(str(e))


def remove_comment_status_from_recipe(recipe_obj):
    """
    method to filter comment status from recipe object
    :param recipe_obj:
    :return:
    """
    try:
        # remove comment status for step level
        excluded_components = ['defaultData', "processFlowImg"]
        list(map(lambda d: d.pop('comment', None), recipe_obj.get('defaultData', {}).get('unitops', [])))
        for step, step_data in recipe_obj.items():
            if step not in excluded_components:
                # remove activity comment status
                list(map(lambda d: d.pop('comment', None), step_data.get('activities', [])))
                for activity, activity_data in step_data.get('activityParams', {}).items():
                    if activity not in ['equipment_class_summary', 'equipment_summary', 'material_summary', "sampling"]:
                        list(map(lambda d: d.pop('comment', None), activity_data.get('params', [])))
                        for param in activity_data.get('params', []):
                            list(map(lambda d: d.pop('comment', None), param.get('fields', [])))
                        for equipment in activity_data.get('equipParams', []):
                            list(map(lambda d: d.pop('comment', None), equipment.get('params', [])))
                            for param in equipment.get('params', []):
                                list(map(lambda d: d.pop('comment', None), param.get('fields', [])))
                        for equipment in activity_data.get('equipmentParameters', []):
                            list(map(lambda d: d.pop('comment', None), equipment.get('params', [])))
                            for param in equipment.get('params', []):
                                list(map(lambda d: d.pop('comment', None), param.get('fields', [])))
                        materials = activity_data.get('materials', {}).get('materialTemplateTableMetaInfo', {}).get(
                            'materialTemplateBodyData', [])
                        list(map(lambda d: d.pop('commentStatus', None), materials))
                        # remove comment status from SR materials
                        sr_materials = activity_data.get('srMaterials', {}).get('materialTemplateTableMetaInfo', {}).get(
                            'materialTemplateBodyData', [])
                        list(map(lambda d: d.pop('commentStatus', None), sr_materials))
                for sampling_table in step_data.get('activityParams', {}).get("sampling", {}).get("sampling", {}).get(
                        'data', {}):
                    samples = sampling_table.get("samplingTemplateTableMetaInfo", {}).get("samplingTemplateBodyData",
                                                                                          [])
                    list(map(lambda d: d.pop('commentStatus', None), samples))
                for sampling_table in step_data.get('activityParams', {}).get("sampling", {}).get("srSampling", {}).get(
                        'data', {}):
                    samples = sampling_table.get("samplingTemplateTableMetaInfo", {}).get("samplingTemplateBodyData",
                                                                                          [])
                    list(map(lambda d: d.pop('commentStatus', None), samples))

    except Exception as ex:
        logger.error(traceback.format_exc())
        logger.error(str(ex))
    return recipe_obj


def fetch_all_shared_recipes(user_id, recipe_type):
    """
    This method is for fetching all the recipes and forming directory structure
    :param user_id: User ID
    :param recipe_type: Recipe Type
    :return: Response after forming directory structure with recipes
    """
    try:
        response = dict()
        modality_list = []
        modality_json = {}
        recipe_records = []

        if recipe_type == "shared":
            recipe_records = canvas_instance_obj.fetch_shared_recipes(recipe_type, user_id)
        # Form modality list
        for each_record in recipe_records:
            modality_list.append(each_record["productFamilyId"])

        # Remove duplicate
        modality_list = list(set(modality_list))

        # Fetch modality records
        modality_records = canvas_instance_obj.fetch_multiple_modality_records(modality_list)

        # Form modality JSON
        for each_record in modality_records:
            modality_json[each_record["id"]] = each_record

        files = []
        # Iterating through each recipe records
        for each_record in recipe_records:
            file_path = each_record.get("selectedFilePath", "/")
            logger.debug("File Path ==> " + str(file_path))
            process_type = each_record.get("processType", "general")
            if process_type == "general":
                type_ = AppConstants.CanvasConstants.general_recipe_type
            elif process_type == "master":
                type_ = AppConstants.CanvasConstants.master_recipe_type
            else:
                type_ = AppConstants.CanvasConstants.site_recipe_type
            try:
                modality = modality_json[each_record["productFamilyId"]]["modalityName"]
            except Exception as e:
                logger.debug(str(e))
                modality = ""

            created_by = each_record.get("userId")
            modified_ts = each_record.get("modified_ts", None)

            process_name = each_record.get("processName", "")
            files.append({"recipeId": each_record["id"],
                          "itemName": "{}.{}".format(process_name, "ps"),
                          "modality": modality,
                          "updated_by": created_by,
                          "dateModified": modified_ts,
                          "modalityId": each_record.get("productFamilyId"),
                          "workspaceType": each_record.get('selectedWorkspaceType'),
                          "flags": type_})

        response['files'] = files
        return response
    except Exception as e:
        logger.error((traceback.format_exc()))
        logger.error(str(e))
        raise Exception(str(e))


def annotations_handler(workspace_record):
    """
    This method fetches all the annotations from a workspace record and transforms it as required by the User Interface
    :param workspace_record: Workspace Record
    :return: Annotations list for a workspace record
    """
    try:
        # Initialize
        steps_json = {}
        annotations_list = []
        omitted_step_keys = ["defaultData"]
        omitted_activity_keys = ["equipments_summary", "equipment_class_summary", "solution_class_summary"]
        
        # Fetch Recipe Object
        recipe_obj = workspace_record.get("recipeObj", {})
        
        process_type = workspace_record.get("processType", "")

        # Iterate through each step and fetch annotations for the steps
        for each_step in recipe_obj.get("defaultData", {}).get("unitops", []):
            steps_json[each_step["id"]] = each_step.get("stepAliasName") or each_step.get("unitopTitle")
            if "annotation" in list(each_step.keys()) and each_step.get("annotation", ""):
                annotations_list.append({"path": each_step.get("stepAliasName", "") or each_step.get("unitopTitle", ""),
                                         "annotation": each_step.get("annotation", "")})

        # Iterate through each step
        for each_step in recipe_obj:

            # Check if step is not in omitted step keys
            if each_step not in omitted_step_keys:

                # Initialize
                activity_json = {}

                # Iterate through each activity and fetch annotations for the activities
                for each_activity in recipe_obj.get(each_step, {}).get("activities", []):
                    activity_name = each_activity.get("activityAliasLabel") or each_activity.get("label")
                    activity_json[each_activity["id"]] = activity_name
                    if "annotation" in list(each_activity.keys()) and each_activity.get("annotation", ""):
                        annotations_list.append({
                            "path": "{}/{}".format(steps_json.get(each_step), activity_name),
                            "annotation": each_activity.get("annotation")
                        })

                for each_sample_template in recipe_obj.get(each_step, {}).get("activityParams", {}).get(
                        "sampling", {}).get("sampling", {}).get("data", []):
                    fields_json = {}
                    for each_field in each_sample_template.get("samplingTemplateTableMetaInfo", {}).get(
                            "samplingTemplateFields", []):
                        fields_json[each_field.get("samplePlanId", "")] = each_field

                    sample_name = each_sample_template.get("samplePlanInfo", {}).get("value", "")
                    sample_count = 1
                    for each_sample_plan in each_sample_template.get("samplingTemplateTableMetaInfo", {}).get(
                            "samplingTemplateBodyData", []):
                        annotation_status = each_sample_plan.get("annotationStatus", {})
                        for attribute_key, annotation in annotation_status.items():
                            if annotation:
                                if process_type == 'experimental':
                                    annotations_list.append({"path": "{}/{}/{}/{}/{}".format(
                                        steps_json.get(each_step),
                                        "Sampling",
                                        sample_name,
                                        sample_count,
                                        fields_json.get(attribute_key, {}).get("samplePlanColumns", "")
                                    ),
                                        "annotation": annotation
                                    })
                                else:
                                    annotations_list.append({"path": "{}/{}/{}/{}/{}".format(
                                        steps_json.get(each_step),
                                        "GRSampling",
                                        sample_name,
                                        sample_count,
                                        fields_json.get(attribute_key, {}).get("samplePlanColumns", "")
                                    ),
                                        "annotation": annotation
                                    })
                        sample_count += 1

                for each_sample_template in recipe_obj.get(each_step, {}).get("activityParams", {}).get(
                        "sampling", {}).get("srSampling", {}).get("data", []):
                    fields_json = {}
                    for each_field in each_sample_template.get("samplingTemplateTableMetaInfo", {}).get(
                            "samplingTemplateFields", []):
                        fields_json[each_field.get("samplePlanId", "")] = each_field

                    sample_name = each_sample_template.get("samplePlanInfo", {}).get("value", "")

                    sample_count = 1
                    for each_sample_plan in each_sample_template.get("samplingTemplateTableMetaInfo", {}).get(
                            "samplingTemplateBodyData", []):
                        annotation_status = each_sample_plan.get("annotationStatus", {})
                        for attribute_key, annotation in annotation_status.items():
                            if annotation:
                                annotations_list.append({"path": "{}/{}/{}/{}/{}".format(
                                    steps_json.get(each_step),
                                    "SRSampling",
                                    sample_name,
                                    sample_count,
                                    fields_json.get(attribute_key, {}).get("samplePlanColumns", "")
                                ),
                                    "annotation": annotation
                                })
                        sample_count += 1
                # Iterate through each activity
                for each_activity in recipe_obj.get(each_step, {}).get("activityParams", {}):

                    # Check if activity not in omitted activity keys
                    if each_activity not in omitted_activity_keys:

                        # Initialize
                        material_template_json = {}

                        # Iterate through each equipment class
                        # Fetch equipment class parameters and attribute annotations
                        for each_equipment_class in recipe_obj.get(each_step, {}).get("activityParams", {}).get(
                                each_activity, {}).get("equipParams", []):
                            equipment_class_name = each_equipment_class.get("equipmentClassName")
                            
                            if process_type in ["site", "experimental"] or \
                                    (process_type == "general" and
                                     each_equipment_class.get("eqClassType", "") != "site"):
                                for each_equipment_class_parameter in each_equipment_class.get("params", []):
                                    equipment_class_parameter_name = each_equipment_class_parameter.get("param_label", "")
                                    if "annotation" in list(each_equipment_class_parameter.keys()) and \
                                            each_equipment_class_parameter.get("annotation", ""):
                                        annotations_list.append({"path": "{}/{}/{}/{}/{}".format(
                                            steps_json.get(each_step),
                                            activity_json.get(each_activity, ""),
                                            "Equipment Class",
                                            equipment_class_name,
                                            equipment_class_parameter_name
                                        ),
                                            "annotation": each_equipment_class_parameter.get("annotation")
                                        })
                                    for each_equipment_class_parameter_attribute in each_equipment_class_parameter.get(
                                            "fields", []):
                                        if "annotation" in list(each_equipment_class_parameter_attribute.keys()) and \
                                                each_equipment_class_parameter_attribute.get("annotation", ""):
                                            annotations_list.append({"path": "{}/{}/{}/{}/{}/{}".format(
                                                steps_json.get(each_step),
                                                activity_json.get(each_activity, ""),
                                                "Equipment Class",
                                                equipment_class_name,
                                                equipment_class_parameter_name,
                                                each_equipment_class_parameter_attribute.get("fieldName", "")
                                            ),
                                                "annotation": each_equipment_class_parameter_attribute.get("annotation")
                                            })

                        # Iterate through each equipment
                        # Fetch Equipment Parameters and Attributes Annotations
                        for each_equipment in recipe_obj.get(each_step, {}).get("activityParams", {}).get(
                                each_activity, {}).get("equipmentParameters", []):
                            equipment_name = each_equipment.get("equipmentName")
                            
                            if process_type == "site":
                                for each_equipment_parameter in each_equipment.get("params", []):
                                    equipment_parameter_name = each_equipment_parameter.get("param_label", "")
                                    if "annotation" in list(each_equipment_parameter.keys()) and \
                                            each_equipment_parameter.get("annotation", ""):
                                        annotations_list.append({"path": "{}/{}/{}/{}/{}".format(
                                            steps_json.get(each_step),
                                            activity_json.get(each_activity, ""),
                                            "Equipment",
                                            equipment_name,
                                            equipment_parameter_name
                                        ),
                                            "annotation": each_equipment_parameter.get("annotation")
                                        })
                                    for each_equipment_parameter_attribute in each_equipment_parameter.get(
                                            "fields", []):
                                        if "annotation" in list(each_equipment_parameter_attribute.keys()) and \
                                                each_equipment_parameter_attribute.get("annotation", ""):
                                            annotations_list.append({"path": "{}/{}/{}/{}/{}/{}".format(
                                                steps_json.get(each_step),
                                                activity_json.get(each_activity, ""),
                                                "Equipment",
                                                equipment_name,
                                                equipment_parameter_name,
                                                each_equipment_parameter_attribute.get("fieldName", "")
                                            ),
                                                "annotation": each_equipment_parameter_attribute.get("annotation")
                                            })

                        # Iterate through each parameter
                        # Fetch Parameters and its attributes annotations
                        for each_parameter in recipe_obj.get(each_step, {}).get("activityParams", {}).get(
                                each_activity, {}).get("params", []):
                            parameter_name = each_parameter.get("param_label", "")
                            
                            if "annotation" in list(each_parameter.keys()) and each_parameter.get(
                                    "annotation", "") and (process_type in ["site", "experimental"] or (
                                            process_type == "general" and each_parameter.get("paramType", "") != "site")
                                                           ):
                                annotations_list.append({"path": "{}/{}/{}/{}".format(
                                    steps_json.get(each_step),
                                    activity_json.get(each_activity, ""),
                                    "Parameter",
                                    parameter_name),
                                    "annotation": each_parameter.get("annotation")
                                })
                            for each_parameter_attribute in each_parameter.get("fields", []):
                                if "annotation" in list(each_parameter_attribute.keys()) and \
                                        each_parameter_attribute.get("annotation", "") and \
                                        (process_type in ["site", "experimental"] or (
                                            process_type == "general" and each_parameter.get("paramType", "") != "site")
                                                           ):
                                    annotations_list.append({"path": "{}/{}/{}/{}/{}".format(
                                        steps_json.get(each_step),
                                        activity_json.get(each_activity, ""),
                                        "Parameter",
                                        parameter_name,
                                        each_parameter_attribute.get("fieldName", "")
                                    ),
                                        "annotation": each_parameter_attribute.get("annotation")
                                    })

                        # Iterate through each material template fields and form material template JSON
                        material_mapping = {"materials": "GRMaterial",
                                            "srMaterials": "SRMaterial"}
                        for material_key, material_label in material_mapping.items():
                            for each_material_template_field in recipe_obj.get(each_step, {}).get("activityParams", {}).get(
                                    each_activity, {}).get(material_key, {}).get("materialTemplateTableMetaInfo", {}).get(
                                    "materialTemplateFields", []):
                                material_template_json[each_material_template_field[
                                    "attributeKey"]] = each_material_template_field["attributeName"]

                            # Iterate through each material data and fetch annotations for each material
                            for each_material in recipe_obj.get(each_step, {}).get("activityParams", {}).get(
                                    each_activity, {}).get(material_key, {}).get("materialTemplateTableMetaInfo", {}).get(
                                    "materialTemplateBodyData", []):
                                annotation_status = each_material.get("annotationStatus", {})
                                for attribute_key, annotation in annotation_status.items():
                                    if annotation:
                                        if process_type == 'experimental':
                                            annotations_list.append({"path": "{}/{}/{}/{}/{}".format(
                                            steps_json.get(each_step),
                                            activity_json.get(each_activity, ""),
                                            'Material',
                                            each_material.get("material_name", "") or
                                            each_material.get("materialID", "") or each_material.get("recipe_name", ""),
                                            material_template_json.get(attribute_key),
                                            ),
                                                "annotation": annotation
                                            })
                                        else:
                                            annotations_list.append({"path": "{}/{}/{}/{}/{}".format(
                                            steps_json.get(each_step),
                                            activity_json.get(each_activity, ""),
                                            material_label,
                                            each_material.get("material_name", "") or
                                            each_material.get("materialID", "") or each_material.get("recipe_name", ""),
                                            material_template_json.get(attribute_key),
                                            ),
                                                "annotation": annotation
                                            })


        return annotations_list
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def fetch_annotations(workspace_id):
    """
    This method fetches all the annotations from a particular workspace
    :param workspace_id: Workspace ID
    :return: List of all annotations for a workspace
    """
    try:
        annotations_json = CommonUtils.get_static_json("canvas_list_annotations",
                                                       AppConfigurations.resources_canvas)
        workspace_record = canvas_instance_obj.fetch_recipe(workspace_id)
        annotations_list = annotations_handler(workspace_record)
        annotations_json["bodyContent"] = annotations_list
        return annotations_json
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def temp_change_sampling_template(sample_plan_data):
    """
    This method is for propagating the sample plan data from old template to new template
    :param sample_plan_data: Sample Plan Data
    :return: Propagated Sample Plan Data
    """
    try:
        response = {"samplingNewData": {}}
        old_sampling_template_fields = sample_plan_data.get("samplingOldTemplateFields", [])
        old_sample_plan_data = sample_plan_data.get("samplingOldData", {})
        old_sample_plan_data["samplingTemplateFields"] = old_sampling_template_fields
        response["samplingNewData"] = old_sample_plan_data
        return response
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def compare_drop_down_values(old_selected_values_list, new_values_list):
    try:

        formatted_new_values_list = []
        for each_selected_value in old_selected_values_list:
            formatted_new_values_list += [item for item in new_values_list if item.get("itemName") ==
                                          each_selected_value.get("itemName", "")]
        return formatted_new_values_list
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def convert_attribute_value_on_type(old_attribute_type, new_attribute_type, value):
    try:
        if old_attribute_type == "calculated_input" and new_attribute_type in ["text", "numeric"]:
            return value.get("value", "")
        elif old_attribute_type in ["text", "numeric", "date"] and new_attribute_type in ["text", "numeric", "date"]:
            return value
        elif old_attribute_type == "drop_down_multiselect" and new_attribute_type in ["text", "numeric"]:
            return ''
        elif old_attribute_type == "drop_down" and new_attribute_type in ["text", "numeric"]:
            new_value = ''
            if len(value) > 0:
                new_value = value[0]["id"]
            return new_value
        elif old_attribute_type in ["text", "numeric", "drop_down_multiselect", "drop_down"] and (
                new_attribute_type == "calculated_input"):
            new_value = {'formulaList': [], 'valueConfig': [], 'enableRelativePosition': False, 'value': 'NA',
                         'formulaInfo': {}}
            return new_value
        else:
            return value
    except Exception as e:
        logger.error(str(e))
        return value


def change_sampling_template(sample_plan_data):
    """
    This method is for propagating the sample plan data from old template to new template
    :param sample_plan_data: Sample Plan Data
    :return: Propagated Sample Plan Data
    """
    try:
        response = {"samplingNewData": {}, "samplePlanInAdd": sample_plan_data.get("samplePlanInAdd", False)}
        material_attributes = ["material_concentration", "material_concentration_unit", "molecular_weight",
                               "molecular_weight_unit", "material_class", "material_group"]
        new_sampling_template_fields = sample_plan_data.get("samplingNewTemplateFields", [])
        old_sample_plan_data = sample_plan_data.get("samplingOldData", {})
        old_fields_names = []
        old_field_ids = []
        new_field_ids = []
        new_fields_name_mapping = {}
        for old_field in old_sample_plan_data.get("samplingTemplateFields", []):
            old_fields_names.append(old_field.get("samplePlanColumns", ""))
            old_field_ids.append(old_field.get("samplePlanId", ""))

        for new_field in new_sampling_template_fields:
            new_fields_name_mapping[new_field["samplePlanId"]] = new_field.get("samplePlanColumns", "")
            new_field_ids.append(new_field.get("samplePlanId", ""))
            
        removable_field_ids = list(set(old_field_ids) - set(new_field_ids))

        for each_sampling_record in old_sample_plan_data.get("samplingTemplateBodyData", []):
            for new_field in new_sampling_template_fields:
                if new_fields_name_mapping[new_field.get("samplePlanId", "")] not in old_fields_names and new_field.get(
                        "samplePlanId, ") not in material_attributes:

                    if new_field.get("samplePlanType", "") in ["drop_down", "drop_down_multiselect"]:
                        each_sampling_record[new_field.get("samplePlanId", "")] = []
                    else:
                        if new_field.get("samplePlanType", "") == "calculated_input":
                            each_sampling_record[new_field.get("samplePlanId", "")] = {
                                "formulaList": each_sampling_record[new_field.get("samplePlanId", "")][
                                    "formulaList"] if each_sampling_record[new_field.get("samplePlanId", "")] else [],
                                "formulaInfo": each_sampling_record[new_field.get("samplePlanId", "")][
                                    "formulaInfo"] if each_sampling_record[new_field.get("samplePlanId", "")] else [],
                                "valueConfig": each_sampling_record[new_field.get("samplePlanId", "")][
                                    "valueConfig"] if each_sampling_record[
                                    new_field.get("samplePlanId", "")] else [],
                                "enableRelativePosition": each_sampling_record[new_field.get("samplePlanId", "")][
                                    "enableRelativePosition"] if each_sampling_record[
                                    new_field.get("samplePlanId", "")] else False,
                                "value": ""
                            }
                        else:
                            each_sampling_record[new_field.get("samplePlanId", "")] = ""
                for old_field in old_sample_plan_data.get("samplingTemplateFields", []):
                    if new_field.get("samplePlanColumns", "").lower() == old_field.get(
                            "samplePlanColumns", "").lower() and old_field.get("samplePlanId", "") != new_field.get(
                            "samplePlanId"):
                        if new_field.get("samplePlanType", "") == old_field.get("samplePlanType", "") and new_field.get(
                                "samplePlanType", "") in ["drop_down", "drop_down_multiselect"]:
                            values_list = new_field.get("values", [])
                            new_values_list = compare_drop_down_values(each_sampling_record.get(
                                old_field.get("samplePlanId", ""), []), values_list)
                            each_sampling_record[new_field.get("samplePlanId", "")] = \
                                each_sampling_record.pop(old_field.get("samplePlanId"), None)
                            each_sampling_record[new_field.get("samplePlanId", "")] = new_values_list

                        elif new_field.get("samplePlanType", "") != old_field.get("samplePlanType", ""):
                            each_sampling_record[new_field.get("samplePlanId", "")] = each_sampling_record.pop(
                                old_field.get("sampling"), None)
                            each_sampling_record[new_field.get("samplePlanId", "")] = convert_attribute_value_on_type(
                                old_field.get("samplePlanType", ""), new_field.get("samplePlanType", ""),
                                each_sampling_record[old_field.get("samplePlanId", "")]
                            )
                        else:
                            each_sampling_record[new_field.get("samplePlanId")] = each_sampling_record.pop(
                                old_field.get("samplePlanId"), None)
                    elif new_field.get("samplePlanColumns", "").lower() == old_field.get(
                            "samplePlanColumns", "").lower() and new_field.get("samplePlanId", "") == old_field.get(
                            "samplePlanId", ""):
                        each_sampling_record[new_field.get("samplePlanId")] = convert_attribute_value_on_type(
                            old_field.get("samplePlanType", ""), new_field.get("samplePlanType", ""),
                            each_sampling_record[new_field.get("samplePlanId", "")]
                         )
                    elif old_field.get("samplePlanId", "") in removable_field_ids:
                        try:
                            each_sampling_record["annotationStatus"].pop(old_field.get("samplePlanId", ""))
                        except Exception as e:
                            logger.error(str(e))
        response["samplingNewData"] = old_sample_plan_data
        return response
    except Exception as e:
        logger.error(traceback.format_exc())
        logger.error(str(e))
        raise Exception(str(e))


def update_value_by_unit_change(input_json):
    """
    :param input_json:
    :return:
    """
    try:
        result = dict()
        result[input_json.get('refering_field')] = input_json.get('to_unit')
        to_unit = input_json.get('to_unit', {}).get('id')
        to_unit_name = input_json.get('to_unit', {}).get('itemName')

        if input_json.get('type') == 'parameter':
            try:
                from_unit = canvas_instance_obj.get_unit_id_by_name(input_json.get('data', {}).get('uom'))
            except Exception as ex:
                logger.error(str(ex))
                raise Exception(str(AppConstants.CanvasConstants.no_matching_conversion_found))
            conversion_mapping = canvas_instance_obj.get_unit_conversion_mapping(from_unit, to_unit)
            if len(conversion_mapping) == 0:
                raise Exception(str(AppConstants.CanvasConstants.no_matching_conversion_found))
            multiplication_factor = conversion_mapping[0].get('multiplication_factor')
            if conversion_mapping[0].get('unit_conversion_from') == from_unit:
                operator = "*"
            elif conversion_mapping[0].get('unit_conversion_from') == to_unit:
                operator = "/"
            for field in input_json.get('data').get('fields', []):
                if field.get('fieldType') == 'numeric':
                    try:
                        field_value = str(eval(str(AppConstants.CanvasConstants.decimal).format(field.get('value'), operator,
                                                                                         str(multiplication_factor))))
                        converted_field_value = str(eval(str(AppConstants.CanvasConstants.decimal_format).format(
                            value=field_value)))
                        field['value'] = str(converted_field_value)
                    except Exception as ex:
                        logger.error('unable to change value')
                elif field.get('fieldType') == "calculated_input":
                    try:
                        tempvalue = field.get("tempValue", "")
                        formula_info = field.get("formulaInfo", {})
                        if tempvalue == "NA" and "multiplication_factor" in field.get("configuration", {}):
                            del field.get("configuration", {})["multiplication_factor"]
                        if formula_info != None and formula_info != {} and tempvalue != "" and tempvalue != "NA":
                            field["configuration"] = field.get("configuration", {})
                            field["configuration"]["multiplication_factor"] = field["configuration"].get(
                                "multiplication_factor", 1)
                            if field["configuration"]["multiplication_factor"] is None:
                                field["configuration"]["multiplication_factor"] = 1
                            
                            field_value = str(eval(str(AppConstants.CanvasConstants.decimal).format(
                                field["configuration"]["multiplication_factor"], operator,  multiplication_factor)))
                            converted_field_value = str(eval(str(AppConstants.CanvasConstants.decimal_format).format(
                            value=field_value)))
                            field["configuration"]["multiplication_factor"] = str(converted_field_value)
                    except Exception as ex:
                        logger.error('unable to change value')
            input_json["data"]["from_unit"] = input_json.get("data").get("uom")
            input_json['data']['uom'] = input_json.get('to_unit', {}).get('uom')
            return input_json['data'], result
        elif input_json.get('type') == 'material':
            data = input_json["data"]
            template = input_json.get('template_info')
            field_type = ""
            attribute_key = ""
            attribute_name = None
            for field in template:
                if field.get('attributeKey') == input_json.get('refering_field'):
                    attribute_name = field.get('attributeName').split(' Units')[0]
                    break
            for field in template:
                if field.get('attributeName') == attribute_name:
                    attribute_key = field.get('attributeKey')
                    field_type = field.get('attributeType')
                    break
            from_unit = ""
            if isinstance(input_json['data'].get(input_json.get('refering_field')), list):
                try:
                    from_unit = input_json.get('data', {}).get(input_json.get('refering_field'))[0]['itemName']
                except:
                    from_unit = ""
                result[input_json.get('refering_field')] = [input_json.get('to_unit')]
            elif isinstance(input_json['data'].get(input_json.get('refering_field')), str):
                from_unit = input_json.get('data', {}).get(input_json.get('refering_field'))
                result[input_json.get('refering_field')] = input_json.get('to_unit', {}).get('itemName')
            if from_unit in [None, ""]:
                result[input_json.get('refering_field')] = [input_json.get('to_unit')]
                return input_json.get('data'), result
            else:
                try:
                    from_unit_name = from_unit
                    from_unit = canvas_instance_obj.get_unit_id_by_name(from_unit)
                except Exception as ex:
                    from_unit = ""
                conversion_mapping = canvas_instance_obj.get_unit_conversion_mapping(from_unit, to_unit)
                if len(conversion_mapping) == 0:
                    raise Exception(str(AppConstants.CanvasConstants.no_matching_conversion_found))
                try:
                    multiplication_factor = conversion_mapping[0].get('multiplication_factor')
                    if conversion_mapping[0].get('unit_conversion_from') == from_unit:
                        operator = "*"
                    elif conversion_mapping[0].get('unit_conversion_from') == to_unit:
                        operator = "/"
                except Exception as ex:
                    raise Exception(str(AppConstants.CanvasConstants.unknown_multiplication_factor))
            if field_type == 'numeric':
                try:
                    field_value = str(eval(str(AppConstants.CanvasConstants.decimal).format(input_json['data'][attribute_key],
                                                                                     operator,
                                                                                     str(multiplication_factor))))
                    converted_field_value = str(eval(str(AppConstants.CanvasConstants.decimal_format).format(
                            value=field_value)))
                    input_json['data'][attribute_key] = str(converted_field_value)
                    result[attribute_key] = input_json['data'][attribute_key]
                except Exception as ex:
                    logger.error(str(ex))
            elif field_type == 'calculated_input':
                try:
                    tempvalue = data.get(attribute_key, {}).get("tempValue", "")
                    formula_info = data.get(attribute_key, {}).get("formulaInfo", {})
                    result[attribute_key] = data.get(attribute_key, {})
                    if tempvalue == "NA" and "multiplication_factor" in data.get(attribute_key, {}).get("valueConfig",
                                                                                                        {}):
                        del data.get(attribute_key, {}).get("valueConfig", {})["multiplication_factor"]
                    if formula_info != None and formula_info != {} and tempvalue != "" and tempvalue != "NA":
                        result[attribute_key]["valueConfig"] = data.get(attribute_key, {}).get("valueConfig", {})
                        result[attribute_key]["valueConfig"]["multiplication_factor"] = data.get(attribute_key, {}).get(
                            "valueConfig", {}).get("multiplication_factor", 1)
                        result["previous_uom"] = from_unit_name
                        result["current_uom"] = to_unit_name
                        if data.get(attribute_key, {}).get("valueConfig", {}).get("multiplication_factor") == None:
                            result[attribute_key]["valueConfig"]["multiplication_factor"] = 1
                    field_value = str(eval(str(AppConstants.CanvasConstants.decimal).format(
                        result[attribute_key]["valueConfig"]["multiplication_factor"],
                        operator, multiplication_factor)))
                    converted_field_value = str(eval(str(AppConstants.CanvasConstants.decimal_format).format(
                        value=field_value)))
                    result[attribute_key]["valueConfig"]["multiplication_factor"] = str(converted_field_value)
                except Exception as ex:
                    logger.error(str(ex))
            result[input_json.get('refering_field')] = [input_json.get('to_unit')]
            return input_json['data'], result
        elif input_json.get('type') == 'sampling':
            data = input_json["data"]
            template = input_json.get('template_info')
            field_type = ""
            attribute_key = ""
            attribute_name = None
            for field in template:
                if field.get('samplePlanId') == input_json.get('refering_field'):
                    attribute_name = field.get('samplePlanColumns').split(' Units')[0]
                    break
            for field in template:
                if field.get('samplePlanColumns') == attribute_name:
                    attribute_key = field.get('samplePlanId')
                    field_type = field.get('samplePlanType')
                    break
            from_unit = ""
            if isinstance(input_json['data'].get(input_json.get('refering_field')), list):
                try:
                    from_unit = input_json.get('data', {}).get(input_json.get('refering_field'))[0]['itemName']
                except:
                    from_unit = ""
                result[input_json.get('refering_field')] = [input_json.get('to_unit')]
            elif isinstance(input_json['data'].get(input_json.get('refering_field')), str):
                from_unit = input_json.get('data', {}).get(input_json.get('refering_field'))
                result[input_json.get('refering_field')] = input_json.get('to_unit', {}).get('itemName')
            if from_unit in [None, ""]:
                result[input_json.get('refering_field')] = [input_json.get('to_unit')]
                return input_json.get('data'), result
            else:
                from_unit = canvas_instance_obj.get_unit_id_by_name(from_unit)
                conversion_mapping = canvas_instance_obj.get_unit_conversion_mapping(from_unit, to_unit)
                if len(conversion_mapping) == 0:
                    raise Exception(str(AppConstants.CanvasConstants.no_matching_conversion_found))
                try:
                    multiplication_factor = conversion_mapping[0].get('multiplication_factor')
                    if conversion_mapping[0].get('unit_conversion_from') == from_unit:
                        operator = "*"
                    elif conversion_mapping[0].get('unit_conversion_from') == to_unit:
                        operator = "/"
                except Exception as ex:
                    raise Exception(str(AppConstants.CanvasConstants.unknown_multiplication_factor))
            if field_type == 'numeric':
                try:
                    field_value = str(eval(str(AppConstants.CanvasConstants.decimal).format(input_json['data'][attribute_key],
                                                                                     operator,
                                                                                     str(multiplication_factor))))
                    converted_field_value = str(eval(str(AppConstants.CanvasConstants.decimal_format).format(
                        value=field_value)))
                    input_json['data'][attribute_key] = str(converted_field_value)
                    result[attribute_key] = input_json['data'][attribute_key]
                except Exception as ex:
                    logger.error(str(ex))
                input_json['data'][input_json.get('refering_field')] = input_json.get('to_unit')
            result[input_json.get('refering_field')] = [input_json.get('to_unit')]
            return input_json.get('data'), result
        else:
            raise Exception("not supported")

    except Exception as e:
        logger.error(traceback.format_exc())
        logger.error(str(e))
        raise Exception(str(e))

def update_value_by_uom_change(input_json):
    """
        :param input_json:
        :return:
        """
    try:
        result = dict()
        result[input_json.get('refering_field')] = input_json.get('to_unit')
        to_unit = input_json.get('to_unit', {}).get('id')

        if input_json.get('type') == 'material':
            attribute_key = 'quantity'
            from_unit = ""
            if isinstance(input_json['data'].get(input_json.get('refering_field')), list):
                try:
                    from_unit = input_json.get('data', {}).get(input_json.get('refering_field'))[0]['itemName']
                except:
                    from_unit = ""
                result[input_json.get('refering_field')] = [input_json.get('to_unit')]
            elif isinstance(input_json['data'].get(input_json.get('refering_field')), str):
                from_unit = input_json.get('data', {}).get(input_json.get('refering_field'))
                result[input_json.get('refering_field')] = input_json.get('to_unit', {}).get('itemName')
            if from_unit in [None, ""]:
                result[input_json.get('refering_field')] = [input_json.get('to_unit')]
                return input_json.get('data'), result
            else:
                try:
                    from_unit = canvas_instance_obj.get_unit_id_by_name(from_unit)
                except Exception as ex:
                    from_unit = ""
                conversion_mapping = canvas_instance_obj.get_unit_conversion_mapping(from_unit, to_unit)
                if len(conversion_mapping) == 0:
                    raise Exception(str(AppConstants.CanvasConstants.no_matching_conversion_found))
                try:
                    multiplication_factor = conversion_mapping[0].get('multiplication_factor')
                    if conversion_mapping[0].get('unit_conversion_from') == from_unit:
                        operator = "*"
                    elif conversion_mapping[0].get('unit_conversion_from') == to_unit:
                        operator = "/"
                except Exception as ex:
                    logger.error(str(ex))
                    raise Exception(str(AppConstants.CanvasConstants.unknown_multiplication_factor))
            field_value = str(eval(str(AppConstants.CanvasConstants.decimal).format(input_json['data'][attribute_key],
                                                                             operator,
                                                                             str(multiplication_factor))))

            converted_field_value = "{:.3f}".format(Decimal(field_value))
            input_json['data'][attribute_key] = str(converted_field_value)
            result[attribute_key] = input_json['data'][attribute_key]
            result[input_json.get('refering_field')] = [input_json.get('to_unit')]
            return input_json['data'], result
        else:
            raise Exception("not supported")

    except Exception as e:
        logger.error(traceback.format_exc())
        logger.error(str(e))
        raise Exception(str(e))

def list_units_for_conversion(input_json):
    """
    :param input_json:
    :return:
    """
    try:
        uom = input_json.get('uom')
        if isinstance(uom, dict):
            uom = uom.get('itemName')
        elif isinstance(uom, list):
            uom = uom[0].get('itemName')
        if uom in ["", None]:
            measure_records = canvas_instance_obj.fetch_unit_records()
        else:
            uom_id = canvas_instance_obj.get_unit_id_by_name(uom)
            records = canvas_instance_obj.fetch_related_unit_list_for_conversion(uom_id)
            unit_id_list = list(set(records))
            try:
                unit_id_list.remove(uom_id)
            except Exception as ex:
                pass # Just passing
            measure_records = canvas_instance_obj.fetch_multiple_unit_records(unit_id_list)
        res_js = []
        for item in measure_records:
            res_js.append({'uom': item.get("UoM", ""), 'id': item.get("id", "")})
        res_js = sorted(res_js, key=lambda k: k.get('uom', '').lower())
        return res_js
    except Exception as ex:
        logger.error(traceback.format_exc())
        logger.error(str(ex))
        return []


def list_quantity_units_for_solution(input_json):
    """
    :param input_json:
    :return:
    """
    try:
        data = input_json.get('data')
        selected_item = input_json.get('selected_item')
        result = []
        if data.get('material_type', "").lower() != 'solution' and selected_item not in ["", None]:
            measure_records = canvas_instance_obj.fetch_unit_records()
            for item in measure_records:
                result.append({'uom': item.get("UoM", ""), 'id': item.get("id", "")})
            result = sorted(result, key=lambda k: k.get('uom', '').lower())
            return result
        elif data.get('material_type', "").lower() == 'solution':
            if selected_item in ["", None]:
                result.append({"UoM": data.get('materialCompositionDetails', {}).get('unit_name'),
                               "id": data.get('materialCompositionDetails', {}).get('unit')})
                return result
            selected_item = data.get('materialCompositionDetails', {}).get('unit_name')
        uom_id = canvas_instance_obj.get_unit_id_by_name(selected_item)

        records = canvas_instance_obj.fetch_related_unit_list_for_conversion(uom_id)
        unit_id_list = list(set(records))
        try:
            unit_id_list.remove(uom_id)
        except Exception as ex:
            pass # Just passing
        measure_records = canvas_instance_obj.fetch_multiple_unit_records(unit_id_list)
        for item in measure_records:
            result.append({'uom': item.get("UoM", ""), 'id': item.get("id", "")})
        result = sorted(result, key=lambda k: k.get('uom', '').lower())
        return result
    except Exception as ex:
        logger.error(str(ex))
        logger.error(traceback.format_exc())
        raise Exception(str(ex))


def fetch_recipes_with_same_recipe_id_for_recent_tab(recipe_id):
    """
        This method is for fetching all recipes with given
        recipe_id ang gives the latest version of of those
        :return: recipe list
    """
    try:
        data_from_collection = canvas_instance_obj.get_latest_version_for_recent(recipe_id)
        return data_from_collection
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def fetch_recipes_with_same_recipe_id_for_recent_tab_major(recipe_id, version_label):
    """
        This method is for fetching all recipes with given
        recipe_id ang gives the latest version of of those
        :return: recipe list
    """
    try:
        data_from_collection = canvas_instance_obj.get_latest_version_for_recent_major(recipe_id, version_label)
        return data_from_collection
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def check_recipe_access(input_json):
    try:
        user_access = True
        full_access_flag = False
        published_flag = False
        message = ""
        if not canvas_instance_obj.check_recipe_is_deleted(input_json.get("recipeId", "")):
            user_access = False
            message = "This recipe has been deleted and is no longer accessible. " \
                      "Please check the Recipe and version linked in the Material table to see if they " \
                      "need to be updated"
            return {"userAccess": user_access, "errorMessage": message}

        recipe_record = canvas_instance_obj.fetch_recipe_record(input_json.get("recipeId", ""))
        if recipe_record.get("viewer_status", False):
            full_access_flag = True

        for each_user_role in recipe_record.get("userGroups", []):
            if each_user_role.get("roleId", "") in ["editor", "viewer"] and input_json.get("userId", "") in \
                    each_user_role.get("users", []):
                full_access_flag = True

        if recipe_record.get("published_details", {}).get('version', 0) >= 1:
            published_flag = True

        if input_json.get("latest_version"):
            workspace_record = canvas_instance_obj.fetch_latest_workspace_using_record_id(
                input_json.get("recipeId", ""))
        else:
            workspace_record = canvas_instance_obj.fetch_workspace_record_by_condition({
                "id": input_json.get("workspaceId", "")})

        workspace_major_version = workspace_record.get("major_version", 0)
        workspace_minor_version = workspace_record.get("version", 0)
        if (published_flag and not full_access_flag and workspace_minor_version > 0 and workspace_major_version > 0)\
                or (not published_flag and not full_access_flag) or \
                (published_flag and not full_access_flag and workspace_major_version <= 0 and workspace_minor_version > 0):
            user_access = False

        if not user_access:
            message = "You are not allowed to view the recipe, since you don't have access to this recipe."
        response = {"userAccess": user_access, "errorMessage": message}
        return response
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def get_material_relations_temp(input_json):
    try:
        material_to_recipe_record = canvas_instance_obj.fetch_material_to_recipe_record_on_condition(input_json)
        recipe_id = material_to_recipe_record.get("recipeId", "") or input_json.get("recipeId", "")
        recipe_record = canvas_instance_obj.fetch_recipe_record(recipe_id)
        response = {
            "materialID": material_to_recipe_record.get("materialID", "") or input_json.get("materialID", ""),
            "material_name": material_to_recipe_record.get("material_name", "") or input_json.get("material_name", ""),
            "recipe_name": recipe_record.get("processName", "") or input_json.get("recipe_name", ""),
            "selectedWorkspaceType": recipe_record.get("selectedWorkspaceType", ""),
            "productFamilyId": recipe_record.get("productFamilyId", ""),
            "recipeType": recipe_record.get("recipeType", ""),
            "selectedFilePath": recipe_record.get("selectedFilePath", ""),
            "workspaceType": recipe_record.get("workspaceType", ""),
            "processType": recipe_record.get("processType", ""),
            "id": recipe_record.get("id", "")
        }
        return response
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def get_material_relations(input_json):
    try:
        material_to_recipe_record = canvas_instance_obj.fetch_material_to_recipe_record_on_condition(
            input_json)
        user_id = input_json.get("userId", "")
        recipe_id = material_to_recipe_record.get("recipeId", "") or input_json.get("recipeId", "")
        material_record = canvas_instance_obj.fetch_material_record_on_condition(
            material_to_recipe_record.get("materialID") or input_json.get("materialID", ""))
        
        uom_record = canvas_instance_obj.fetch_uom_record(material_record.get("unit", ""))
        
        if material_record and uom_record:
            material_record["unit"] = [{"id": uom_record.get("id", ""), "itemName": uom_record.get("UoM")}]
        if not material_record:
            material_record = {"materialID": material_to_recipe_record.get("materialID", ""),
                               "material_name": material_record.get(
                                   "material_name", "") or material_to_recipe_record.get("material_name", "")}
        recipe_record = canvas_instance_obj.fetch_recipe_record(recipe_id)

        for each_user_role in recipe_record.get("userGroups", []):
            if each_user_role.get("roleId", "") == "editor" and user_id in each_user_role.get("users", []):
                recipe_record["fullAccess"] = True
                
        response = {
            "recipeData": recipe_record,
            "materialData": material_record
        }
        return response
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def update_recipe_metadata(input_json):
    try:
        type_ = "rename_metadata"
        logger.debug(str(AppConstants.CanvasConstants.type_logger) + str(type_))
        recipe_id = input_json.get('recipeId', '')

        metadata = input_json.get('metadata', {})
        material_to_recipe_record = {}
        if metadata.get("materialRowId", ""):
            material_to_recipe_record = canvas_instance_obj.fetch_material_to_recipe_record_using_material_id(
                metadata.get("materialRowId", ""))
            
        material_record = canvas_instance_obj.fetch_material_record_on_condition(metadata.get('materialID', ''))

        metadata.update({"materialID": metadata.get("materialID", "") or material_record.get("materialID", ""),
                         "material_name": metadata.get("material_name", "") or material_record.get("material_name", ""),
                         "materialRowId": metadata.get("materialRowId", "") or material_record.get("materialRowId", "")
                         })
        
        if canvas_instance_obj.check_material_is_already_associated(
                recipe_id,
                metadata.get("materialID", "")):
            warning_message = str(AppConstants.CanvasConstants.material_already_associated)
            response = error_obj.result_error_template(message=warning_message, error_category="Warning")
            
        elif canvas_instance_obj.check_material_name_changes_for_recipe_metadata(
                metadata.get("materialID", ""), metadata.get("material_name", "")):
            warning_message = "Material Name Cannot be Changed as Material ID is already configured with another " \
                              "Material Name in Master Data Configuration"
            response = error_obj.result_error_template(message=warning_message, error_category="Warning")
        
        else:
            # old_recipe_record = canvas_instance_obj.fetch_recipe_record(recipe_id)
            # logger.debug("Old Recipe Record == > " + str(old_recipe_record))
            # canvas_instance_obj.update_material_to_recipe_metadata(recipe_id, metadata)
            # canvas_instance_obj.update_recipe_metadata(recipe_id, {"materialID": metadata.get("materialID", "") or
            #                                                                      material_record.get(
            #                                                                          "materialID", ""),
            #                                                        "material_name": metadata.get("material_name", "") or
            #                                                                         material_record.get(
            #                                                                             "material_name", ""),
            #                                                        "materialRowId": metadata.get("materialRowId", "") or
            #                                                                         material_record.get(
            #                                                                             "materialRowId", "")})
            # canvas_instance_obj.update_material_info(recipe_id, metadata.get("materialRowId", ""))
            message = "Valid Material"
            #
            # if material_to_recipe_record:
            #     metadata = {'materialID': material_to_recipe_record.get("materialID", "") or
            #                                                                      material_record.get(
            #                                                                          "materialID", ""),
            #                 'material_name': material_to_recipe_record.get("material_name", "") or material_record.get(
            #                                                                             "material_name", ""),
            #                 "materialRowId": material_to_recipe_record.get("materialRowId", "") or material_record.get(
            #                                                                             "materialRowId", "")}

            response = {"status": "OK", "message": message, "freeTextId": input_json.get("freeTextId", ""),
                        "freeTextName": input_json.get("freeTextName", ""), "metadata": metadata}
            AuditManagementAC.save_audit_entry()
        return response
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def fetch_necessary_recipe_related_details(input_data):
    '''
    Author : Anson
    :param input_data:
    :return:
    '''
    try:
        flag = False
        recipe_id = input_data["recipe_id "]
        # fetch data from recipe collection
        required_recipe = fetch_recipes_with_recipe_id_from_recipe_col(recipe_id)
        # fetch recipe collection based on recipe_id
        # recipe = get_recipe_from_recipe_col(recipe_id)
        viewer_status = required_recipe.get('viewer_status', False)
        split_version = required_recipe.get("version_label", '').split('.')
        major_version = split_version[0] + '.0.0'
        for user in required_recipe.get('userGroups', ""):
            for users in user.get('users', ''):
                if users == input_data.get('user_id', ''):
                    flag = True
                    break

        if viewer_status or flag:
            # fetch_recipes_with_same_recipe_id_for_recent_tab will give the latest version of recipe
            latest_recipe = fetch_recipes_with_same_recipe_id_for_recent_tab(recipe_id)
            workspace_id = latest_recipe["id"]
            # Form response JSON
            result_json = {"processName": required_recipe.get("processName"),
                           "recipeType": required_recipe.get("recipeType"),
                           "processType": required_recipe.get("processType"),
                           "selectedWorkspaceType": required_recipe.get("selectedWorkspaceType"),
                           "selectedFilePath": required_recipe.get("selectedFilePath"),
                           "modalityId": latest_recipe.get("modalityId"),
                           "recipeId": recipe_id,
                           "workspaceId": workspace_id,
                           "version": latest_recipe.get("version_label")
                           }
            return result_json
        elif split_version[0][:] == 'v0':
            return False
        else:
            # fetch recipe based on major version
            latest_recipe = fetch_recipes_with_same_recipe_id_for_recent_tab_major(recipe_id, major_version)
            if latest_recipe:
                workspace_id = latest_recipe[0].get("id", '')
            required_recipe = fetch_recipes_with_recipe_id_from_recipe_col(recipe_id)
            result_json = {"processName": required_recipe.get("processName", ""),
                           "recipeType": required_recipe.get("recipeType", ""),
                           "processType": required_recipe.get("processType", ""),
                           "selectedWorkspaceType": required_recipe.get("selectedWorkspaceType", ""),
                           "selectedFilePath": required_recipe.get("selectedFilePath", ""),
                           "modalityId": latest_recipe[0].get("modalityId", ""),
                           "recipeId": recipe_id,
                           "workspaceId": workspace_id,
                           "version": latest_recipe[0].get("version_label", "")
                           }
            return result_json
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def fetch_recipes_with_recipe_id_from_recipe_col(recipe_id):
    """
    This method is for fetching all recipes with given
    recipe_id from recipe collection for forming json
    :return: recipe list
    """
    try:
        data_from_collection = canvas_instance_obj.fetch_recipe_collection_by_id(recipe_id)
        return data_from_collection

    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def fetch_all_comments(input_json):
    """
    This method fetches all the annotations from a particular workspace
    :param workspace_id: Workspace ID
    :return: List of all annotations for a workspace
    """
    try:
        recipe_version = input_json.get('version')
        major_version = input_json.get('version').split('.')[0]
        workspace_id = input_json.get('workspaceId')
        logger.debug("Workspace Id ==> " + str(workspace_id))
        version_string = ""
        recipe_id = input_json.get('recipeId')
        user_id = input_json.get('userId')
        comments_records, users_list = canvas_instance_obj.fetch_all_comments(recipe_id, major_version, version_string, user_id)

        workspace_record = input_json.get('recipeObj', {})
        comments_list = RecipeCommentsAC.comments_handler(comments_records, workspace_record, users_list, user_id,
                                                          recipe_version)

        return comments_list
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def check_recipe_edit_access(recipe_id, user_id):
    """
    check for a user is in editor list
    :param recipe_id:
    :param user_id:
    :return:
    """
    try:
        user_access = True
        if not canvas_instance_obj.check_recipe_access_on_user_role(recipe_id, user_id):
            user_access = False
        return user_access
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def get_gr_data_for_sr(workspace_id, user_id, check_access=True):
    """
    check new version of GR is available for SR
    :param workspace_id:
    :return: updated_status, updated_workspace_id
    """
    try:
        workspace_data = canvas_instance_obj.fetch_recipe(workspace_id)
        recipe_id = workspace_data.get('recipeId')
        workspace_list = canvas_instance_obj.get_sorted_workspace_version(recipe_id)
        if workspace_list[0].get('id') != workspace_id:
            return False, {}
        if not workspace_data.get("processType", "") == "site":
            return False, {}
        if check_access:
            if not check_recipe_edit_access(recipe_id, user_id):
                return False, None
            if not workspace_data.get('workFlowReviewObj', {}).get('editable', True) or workspace_data.get('workFlowReviewObj', {}).get('reviewState', False):
                return False, {}
        template_id = workspace_data.get('workspaceTemplateId')
        if not template_id:
            return False, {}
        workspace_data = canvas_instance_obj.fetch_recipe(template_id)
        recipe_id = workspace_data.get('recipeId')
        recipe_data = canvas_instance_obj.fetch_record_by_id(AppConfigurations.recipe_collection, recipe_id)
        fetch_all = False
        if recipe_data.get('viewer_status'):
            fetch_all = True
        else:
            for group in recipe_data.get('userGroups', []):
                if user_id in group.get('users', []):
                    fetch_all = True
                    break
        versions_list = []
        if fetch_all:
            versions_list.append(recipe_data.get('version_label'))
        else:
            version_list = recipe_data.get('version_label').split(".")
            major_version = version_list[0][1:] or 0
            if int(major_version) < 1:
                return False, workspace_data
            versions_list.append(AppConstants.public_recipe_version_format.format(str(major_version)))
            versions_list.append(AppConstants.shared_recipe_version_format.format(major_version=str(major_version),version=str(0), minor_version=str(0)))
        condition = {"recipeId": recipe_id, "version_label": {"$in":versions_list}}
        workspace_data = canvas_instance_obj.fetch_workspace_record_by_condition(condition)
        return True, workspace_data

    except Exception as ex:
        logger.error(traceback.format_exc())
        logger.error(str(ex))
        raise Exception(str(ex))


def get_workspace_data_from_approved(workspace_data):
    """
    :param workspace_data:
    :return:
    """
    try:
        return canvas_instance_obj.create_recipe_from_approved(workspace_data)
    except Exception as ex:
        logger.error(str(ex))
        raise Exception(str(ex))


def fetch_sampling_attributes(sample_plan_id):
    try:
        response_list = []
        if sample_plan_id == "parameter":
            parameters_list = []
            measure_list = []
            measure_json = {}
            sampling_parameter_records = canvas_instance_obj.fetch_all_sampling_parameter_records()
            for each_record in sampling_parameter_records:
                parameters_list.append(each_record.get("parameter", ""))

            parameter_records = canvas_instance_obj.fetch_multiple_parameter_records(parameters_list)
            for each_record in parameter_records:
                measure_list.append(each_record.get("uom", ""))

            measure_records = canvas_instance_obj.fetch_multiple_uom_records(measure_list)
            for each_record in measure_records:
                measure_json[each_record["id"]] = each_record

            for each_record in parameter_records:
                uom_available = False
                uom_value = ""
                if each_record.get("uom", ""):
                    uom_available = True
                    uom_value = measure_json.get(each_record.get("uom", ""), {}).get("UoM", "")
                response_list.append({"id": each_record.get("id", ""),
                                      "itemName": each_record.get("parameterName"),
                                      "uomAvailable": uom_available,
                                      "uomValue": uom_value})
        elif sample_plan_id == "time":
            time_list = []
            time_records = canvas_instance_obj.fetch_all_sample_plan_time_records()
            for each_record in time_records:
                time_list.append(each_record.get("time", ""))
            for each_time_value in time_list:
                response_list.append({"id": each_time_value,
                                      "itemName": each_time_value})

        elif sample_plan_id == "analytical_method":
            analytical_method_list = []
            analytical_method_records = canvas_instance_obj.fetch_all_sample_plan_analytical_method_records()
            for each_record in analytical_method_records:
                analytical_method_list.append(each_record.get("analyticalMethod", ""))

            for each_analytical_method in analytical_method_list:
                response_list.append({"id": each_analytical_method,
                                      "itemName": each_analytical_method})
        return response_list
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def get_compare_screen_data_temp(sr_workspace_id, user_id):
    """
    :param template_id:
    :param sr_workspace_id:
    :param propagate:
    :return:
    """
    try:
        status, gr_workspace_data = get_gr_data_for_sr(sr_workspace_id, user_id)
        if not status:
            return {}, "", False

        # Fetch GR and SR Recipe Obj
        sr_workspace_data = recipe_propagation_instance_obj.fetch_recipe(sr_workspace_id)
        if sr_workspace_data.get('recipeType', "").lower() == 'shared':
            sr_workspace_data = CollaborationManagementAC.view_selected_shared_workspace(sr_workspace_id, user_id)
        if gr_workspace_data.get('recipeType', "").lower() == 'shared':
            gr_workspace_data = CollaborationManagementAC.view_selected_shared_workspace(gr_workspace_data.get('id'),
                                                                                         user_id)
        status, gr_workspace_data = get_workspace_data_from_approved(gr_workspace_data)
        if not status:
            return {}, {}, False
        sr_recipe_obj = sr_workspace_data.get('recipeObj')
        gr_recipe_obj = gr_workspace_data.get('recipeObj')
        status, new_steps_added = get_recipes_update_status(gr_recipe_obj, sr_recipe_obj)
        return status, gr_workspace_data.get('id'), new_steps_added
    except Exception as ex:
        logger.error(traceback.format_exc())
        logger.error(str(ex))
        logger.error(traceback.format_exc())
        raise Exception(str(ex))


def get_recipes_update_status(r1_data, r2_data):
    """
    :param r1_data:
    :param r2_data:
    :param r1_type:
    :param r2_type:
    :return:
    """

    # iterate through each unitop and compare
    try:
        step_status = {}
        status = False
        if not r2_data:
            r2_data = {}
        if not r1_data:
            r1_data = {}
        for (r1_step, r2_step) in itertools.zip_longest(r1_data.get('defaultData', {}).get('unitops', []),
                                                        r2_data.get('defaultData', {}).get('unitops', []),
                                                        fillvalue={}):

            if not r1_step.get('step_id') and not r2_step.get('step_id'):
                continue
            r1_step = copy.deepcopy(r1_step)
            r2_step = copy.deepcopy(r2_step)

            r1_step_data = r1_data.get(r1_step.get('step_id'), {})
            r2_step_data = r2_data.get(r2_step.get('step_id'), {})
            with concurrent.futures.ThreadPoolExecutor() as executor:
                thread_status = executor.submit(get_step_update_status, r1_step, r2_step, r1_step_data, r2_step_data)
                status = thread_status.result()

            step_status[r2_step.get('step_id')] = status
        new_steps = False
        for step in r1_data.get('defaultData', {}).get('unitops', []):
            if step.get('id') not in r2_data:
                new_steps = True
                break
        return step_status, new_steps
    except Exception as ex:
        logger.error(str(ex))
        logger.error(traceback.format_exc())
        raise Exception(str(ex))


def move_recipes(input_json):
    """
    This method is for moving recipe from source to destination path
    :param input_json: JSON containing Recipe Source Path, Destination Path, Recipe ID and Process Name
    :return: Message Whether Recipe Successfully moved or Not
    """
    try:
        # Check if recipe already exists at the destination
        # If Recipe already exists send warning message to the user
        if canvas_instance_obj.check_recipe_already_exists(input_json):
            message = "This destination already contains a recipe named '{recipe_name}'".format(
                recipe_name=input_json.get("processName", ""))
            response = error_obj.result_error_template(message=message, error_category="Warning")
            return response
        
        # Form JSON to fetch accessible shared recipes
        accessible_shared_recipes_json = {"selectedFilePath": input_json.get("sourcePath", ""),
                                          "userId": input_json.get("userId", ""),
                                          "recipeType": input_json.get("recipeType", ""),
                                          "recipeId": input_json.get("recipeId", "")}
        
        # Fetch Published Recipe Records
        published_recipe_records = canvas_instance_obj.fetch_published_records_using_recipe_id(
            input_json.get("recipeId", ""))
        
        # Fetch Accessible Recipe Records
        accessible_recipe_records = canvas_instance_obj.fetch_accessible_shared_recipes_using_recipe_id(
            accessible_shared_recipes_json)
        
        # Check if it is published record or not accessible record
        # If Published or Not Accessible Record Send warning message to the user
        if published_recipe_records or not accessible_recipe_records:
            warning_message = "Unable to Move the Recipe, Unauthorized to Perform the Action or Recipe has Published" \
                              "/Approved/Major Version in its Lineage"
            response = error_obj.result_error_template(message=warning_message, error_category="Warning")
            return response
        
        # Move the recipe if all required permissions are available
        canvas_instance_obj.move_recipe_for_recipe_collection(input_json)
        start_new_thread(canvas_instance_obj.move_recipe_except_recipe_collection, (input_json,))
        message = "Successfully moved the recipe!"
        response = {"status": "OK", "message": message}
        return response
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def move_folder(input_json):
    """
    This method is for moving folder and its contents from one location to another
    :param input_json: JSON containing source path and destination path for moving recipes and folders
    :return: Message whether folder successfully moved or not
    """
    try:
        # Initialize
        recipe_and_new_path_mapping_json = {}
        
        # Source Path
        source_path = input_json.get("sourcePath", "")
        
        # Fetch Destination Path
        destination_path = input_json.get("destinationPath", "")
        
        # Check if folder already exists in the destination
        # If already exists send warning message to the user
        if canvas_instance_obj.check_folder_already_exists(input_json):
            folder_name = input_json.get("sourcePath", "").split("/")[-1]
            message = str(AppConstants.CanvasConstants.already_contains_folder_named).format(
                folder_name=folder_name)
            response = error_obj.result_error_template(message=message, error_category="Warning")
            return response
        
        # Form Accessible Recipes JSON
        accessible_recipes_json = {"selectedFilePath": input_json.get("sourcePath", ""),
                                   "userId": input_json.get("userId", ""),
                                   "recipeType": input_json.get("recipeType", "")}
        
        # Fetch Recipe Records
        recipe_records = canvas_instance_obj.fetch_recipe_records_based_on_file_path(
            input_json.get("sourcePath", ""), input_json.get("recipeType", ""))
        
        # Fetch Published and Accessible Recipe Records
        published_recipe_records, accessible_recipe_records = canvas_instance_obj.fetch_accessible_shared_recipes(
            accessible_recipes_json)
        
        # Check if folder contains any published recipe records and if any record is not accessible record
        if published_recipe_records or len(recipe_records) != len(accessible_recipe_records):
            warning_message = "Unable to Move the Folder, Unauthorized to Perform the Action or Recipe " \
                              "has Published/Approved/Major Version in its Lineage"
            response = error_obj.result_error_template(message=warning_message, error_category="Warning")
            return response
        
        # Fetch Recipe Records
        recipe_records = canvas_instance_obj.fetch_all_records_with_relative_path(input_json)
        
        # Iterate through each recipe record and form recipe to new path mapping JSON
        for each_record in recipe_records:
            if destination_path != "/":
                source_path_split = source_path.split("/")
                source_path_split = list(filter(None, source_path_split))
                source_path_len = len(source_path_split)
                folder_path_split = each_record.get("selectedFilePath", "").split("/")
                folder_path_split = list(filter(None, folder_path_split))
                try:
                    folder_path_split = folder_path_split[source_path_len - 1:]
                except Exception as e:
                    folder_path_split = []
                folder_path = "{destination_path}/{relative_path}".format(destination_path=destination_path,
                                                                          relative_path="/".join(folder_path_split))
                recipe_and_new_path_mapping_json[each_record.get("id", "")] = "{destination_path}".format(
                    destination_path=folder_path)
            else:
                source_path_split = source_path.split("/")
                source_path_len = len(source_path_split)
                folder_path_split = each_record.get("selectedFilePath", "").split("/")
                folder_path_split = list(filter(None, folder_path_split))
                try:
                    folder_path_split = folder_path_split[source_path_len - 2:]
                except Exception as e:
                    folder_path_split = []
                
                folder_path = "/{relative_path}".format(relative_path="/".join(folder_path_split))
                
                recipe_and_new_path_mapping_json[each_record.get("id", "")] = "{destination_path}".format(
                    destination_path=folder_path)
        
        # Update Multiple Recipe Location in Recipe Collection
        canvas_instance_obj.update_recipes_location_metainfo_for_recipe_collection(recipe_and_new_path_mapping_json)
        
        # Update Multiple Recipe Location Except Recipe Collection
        start_new_thread(canvas_instance_obj.update_recipes_location_metainfo_except_recipe_collection, (
            recipe_and_new_path_mapping_json,))
        message = "Successfully moved the folder!"
        response = {"status": "OK", "message": message}
        return response
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def move_recipes_and_folders(input_json, user_role_code_list):
    """
    This method is for moving recipes and folders from source path to destination path
    :param input_json: JSON containing source path and destination path for moving recipes and folders
    :param user_role_code_list: User Role Code List
    :return: Message whether recipes or folders are moved successfully
    """
    try:
        # Initialize
        type_ = "move"
        response = {}
        logger.debug(str(AppConstants.CanvasConstants.type_logger) + str(type_))
        
        # If Resource Type is Recipe
        if input_json.get("resourceType", "") == "recipe":
            response = move_recipes(input_json)
            
        # If Resource Type is Folder
        elif input_json.get("resourceType", "") == "folder":
            response = move_folder(input_json)
            
        # JSON to fetch Hierarchy Details
        recipe_json = {"userId": input_json.get("userId", ""), "recipeType": input_json.get("recipeType", ""),
                       "searchKey": input_json.get("searchKey", ""), "searchField": input_json.get("searchField", {}),
                       "selectedFilePath": input_json.get("selectedFilePath", "")}
        
        # Recipes and Folders List
        hierarchy_details = fetch_all_recipes_temp3(recipe_json, user_role_code_list)
        response["hierarchyDetails"] = hierarchy_details
        if response.get("status", "").lower() == "ok":
            AuditManagementAC.save_audit_entry()
        return response
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def version_patches(version_number, level=None):
    """
    :param level
    :param version_number:
    :return:
    """
    try:
        if not version_number:
            return []
        if level == 'first':
            version_number = version_number.split('.')
            version_number = '.'.join(version_number[:1])
        elif level == 'second':
            version_number = version_number.split('.')
            version_number = '.'.join(version_number[:2])
        response = canvas_instance_obj.fetch_patches_from_version(version_number, level)
        return response
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def list_step_details_for_site_recipe_creation(input_json):
    """

    :param input_json:
    :return:
    """
    try:
        workspace_id = input_json.get("grWorkspaceId")
        recipe_id = input_json.get("grRecipeId")
        if not workspace_id:
            workspace_id = fetch_latest_workspace_id(recipe_id)
        workspace_data = canvas_instance_obj.get_approved_workspacedata(workspace_id)
        site_list = input_json.get('sites', [])
        try:
            site_list = [site.get('id') for site in site_list]
        except Exception as ex:
            logger.error(str(ex))

        result = []
        for step in workspace_data.get('recipeObj', {}).get('defaultData', {}).get('unitops', []):
            data = dict()
            step_id = step.get('stepRefId') or step.get('step_id')
            data['stepId'] = step.get('step_id')
            data['stepRefId'] = step.get('stepRefId') or step.get('step_id')
            data['stepName'] = step.get('stepAliasName') or step.get('unitopTitle')
            sr_template_for_step_obj = canvas_instance_obj.check_approved_sr_template_exist_for_step(step_id, site_list)
            if sr_template_for_step_obj.get("status",False) and not sr_template_for_step_obj.get("count")>1:
                input_data = dict()
                input_data["stepId"] = step.get('step_id')
                input_data["sites"] = input_json.get('sites', [])
                input_data["filterType"] = 'sites'
                response = ConfigurationManagementAC.fetch_all_site_template_details_for_step(input_data)
                for each_obj in response:
                    if each_obj.get("templateId") == "default":
                        response.remove(each_obj)
                data["template_details"] = response[0]
            data['configured'] = sr_template_for_step_obj.get("status",False)
            result.append(copy.deepcopy(data))
        return result
    except CreateProjectException as ex:
        raise CreateProjectException(str(ex))
    except Exception as ex:
        logger.error(str(ex))
        raise Exception("Unable to find step details")


def fetch_recipe_sites(input_json):
    """
    This method fetches sites based on the recipe
    For Site recipe it fetches the preferred sites
    For General recipe it fetches all the sites
    :param input_json: Contains information about the recipe
    :return: Site Details related to the recipe
    """
    try:
        # Fetch Recipe ID
        recipe_id = input_json.get("recipeId", "")
        
        # Fetch Recipe Record
        recipe_record = canvas_instance_obj.fetch_recipe_record(recipe_id)
        
        # Fetch Preferred sites
        preferred_sites = recipe_record.get("sites", None)
        
        # Fetching site details from Persistence DB
        site_records = canvas_instance_obj.fetch_site_details(preferred_sites)
        
        # Fetch Site Details
        site_details = fetch_default_site_details(site_records)
        
        # Form Response
        response = {"recipeObj": {}, "siteData": {"locationsData": site_details}, "facilityFitReloadedTime": ""}
        return response
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def fetch_default_site_details(site_details):
    """
    This method is for fetching site details
    :return: Site details for facility fit
    """
    try:
        # Initialize
        location_data = []
        
        # Fetch color coding JSON
        color_coding = get_color_coding_info()
        logger.debug("Color Coding == >" + str(color_coding))
        
        # Iterate through each site
        for site in site_details:
            
            # Fetch Site ID
            site_id = site.get("id", "")
            
            # Form Site Color Information JSON
            site_color_info = {'siteColor': 'badge-default', 'siteId': site_id,
                               'siteCode': site['Site']}
            
            # Initialize
            building_details = []
            building_list = []
            line_list = []
            building_json = dict()
            line_json = dict()
            
            # Fetch Links, Contains Building ID and Lines related to each building
            links = site.get("links", {})
            
            # Iterate through each Building
            for building_id, line_id_list in list(links.items()):
                
                # Add Building ID to building list
                building_list.append(building_id)
                
                # Iterate though each line
                for each_line in line_id_list:
                    # Add line Id to line list
                    line_list.append(each_line)
            
            # Fetch Building records
            building_records = canvas_instance_obj.fetch_multiple_building_records(building_list)
            
            # Fetch Line records
            line_records = canvas_instance_obj.fetch_multiple_line_records(line_list)
            
            # Iterate through each building record and form building JSON
            for each_record in building_records:
                building_json[each_record["id"]] = each_record
            
            # Iterate through each line record and form line JSON
            for each_record in line_records:
                line_json[each_record["id"]] = each_record
            
            # Iterate through each building
            for building_id, line_id_list in list(links.items()):
                
                # Form Building information JSON
                building_info = {"building_name": building_json[building_id].get("buildingName", ""),
                                 "building_color": "badge-default",
                                 "building_id": building_id,
                                 "line_details": []}
                
                # Iterate through each line
                for each_line in line_id_list:
                    # Form Line information JSON
                    line_info = {"line_name": line_json[each_line].get("name", ""),
                                 "line_id": each_line,
                                 "line_color": "badge-default"}
                    
                    # Add Line information to line details
                    building_info["line_details"].append(line_info)
                
                # Add Building information to building details list
                building_details.append(building_info)
            site_color_info["building_details"] = building_details
            
            # Add Site color Information to Location data
            location_data.append(site_color_info)
        return location_data
    except Exception as e:
        print((traceback.format_exc()))
        logger.error(str(e))
        raise Exception(str(e))


def get_color_coding_info():
    """
    returns the color coding details for facility fit
    :return:
    """
    try:
        # Color coding
        color_coding = {'red': {'label': 'badge-danger', 'code': '#dd0000'},
                        'green': {'label': 'badge-success', 'code': '#0a7e83'},
                        'yellow': {'label': 'badge-warning', 'code': '#f8cb00'}}
        return color_coding
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def list_all_experiment_recipe():
    """

    :return:
    """
    try:
        experiment_recipe_list = list()
        list_of_experiment_recipes = canvas_instance_obj.fetch_experiment_recipe_list()
        for recipe in list_of_experiment_recipes:
            experiment_recipe_list.append({
                "id": recipe["id"],
                "name": recipe["processName"]
            })
        return experiment_recipe_list
    except Exception as ex:
        logger.error(str(ex))
        print(traceback.format_exc())


def update_recipe_decorator_for_general_parameters(template_data):
    """
    
    :param template_data: 
    :return: 
    """
    recipe_decorator = {"steps": {}}
    try:
        for step_id, step_data in template_data.items():
            if not recipe_decorator['steps']:
                recipe_decorator['steps'] = {step_id: {"activities": {}}}
            activities_decorator_obj = recipe_decorator['steps'][step_id]['activities']
            for each_activity in step_data.get("activityParams", {}):
                if each_activity not in ["sampling", "equipment_class_summary", "equipments_summary",
                                             "solution_class_summary"] and each_activity not in activities_decorator_obj:
                        activities_decorator_obj[each_activity] = {"params": {}}
                        for param in step_data.get("activityParams", {}).get(each_activity, {}).get('params', []):
                            param_decorator_obj = dict()
                            if param.get('paramType', "general") == "general":
                                for field in param.get('fields', []):
                                    param_decorator_obj[field.get('fieldId')] = {"source": {
                                        "srAttributeChange": True,
                                        "infoMessage": "This attribute was added in the General Recipe Step Template",
                                        "fromConfig": True
                                    }}
                                activities_decorator_obj[each_activity]['params'][param.get('id')] = {"fields": param_decorator_obj}

        return recipe_decorator
    except Exception as ex:
        logger.error(str(ex))
        logger.error(traceback.format_exc())
        return recipe_decorator


def update_decorator_obj_for_parameter(gr_workspace_data, sr_workspace_data):
    """

    :param gr_workspace_data:
    :param sr_workspace_data:
    :return:
    """
    try:
        gr_recipe_obj = gr_workspace_data.get('recipeObj', {})
        sr_recipe_obj = sr_workspace_data.get('recipeObj', {})
        decorator_obj = sr_workspace_data.get('recipeDecorators', {})
        if 'steps' not in decorator_obj:
            decorator_obj['steps'] = dict()
        for step in sr_recipe_obj.get('defaultData', {}).get('unitops', []):
            step_id = step.get('id')
            if step_id not in decorator_obj['steps']:
                decorator_obj['steps'][step_id] = {"activities": dict()}
            if step_id in gr_recipe_obj:
                update_decorator_obj_for_step(gr_recipe_obj.get(step_id, {}), sr_recipe_obj.get(step_id), decorator_obj.get('steps', {}).get(step_id, {}))
    except Exception as ex:
        logger.error(str(ex))
        print(traceback.format_exc())


def update_decorator_obj_for_step(gr_step_data, sr_step_data, step_decorator_obj):
    """

    :param gr_step_data:
    :param sr_step_data:
    :param step_decorator_obj:
    :return:
    """
    try:
        omitted_activities = ["equipment_class_summary", "solution_class_summary", "sampling", "equipment_summary"]
        if 'activities' not in step_decorator_obj:
            step_decorator_obj['activities'] = dict()
        for activity in sr_step_data.get('activities', []):
            activity_id = activity.get('id')
            if activity_id not in step_decorator_obj['activities']:
                step_decorator_obj['activities'][activity_id] = {'params': dict()}
            if activity_id in gr_step_data.get('activityParams', {}) and activity_id not in omitted_activities:
                update_decorator_obj_for_activity(gr_step_data.get('activityParams', {}).get(activity_id, {}), sr_step_data.get('activityParams', {}).get(activity_id, {}), step_decorator_obj.get('activities', {}).get(activity_id, {}))
    except Exception as ex:
        logger.error(str(ex))
        print(traceback.format_exc())


def update_decorator_obj_for_activity(gr_activity, sr_activity, activity_decorator_obj):
    """

    :param gr_activity:
    :param sr_activity:
    :param activity_decorator_obj:
    :return:
    """
    try:
        gr_parameter_mapping = dict()
        for parameter in gr_activity.get('params', []):
            if parameter.get('paramType', "general") == "general":
                gr_parameter_mapping[parameter.get('id')] = parameter
        for parameter in sr_activity.get('params', []):
            if parameter.get('paramType', "general") == "general" and parameter.get('id') in gr_parameter_mapping:
                if parameter.get('id') not in activity_decorator_obj['params']:
                    activity_decorator_obj['params'][parameter.get('id')] = {'fields': {}}
                update_decorator_obj_for_field(gr_parameter_mapping.get(parameter.get('id')), parameter, activity_decorator_obj.get('params', {}).get(parameter.get('id'), {}))
    except Exception as ex:
        logger.error(str(ex))
        print(traceback.format_exc())


def update_decorator_obj_for_field(gr_parameter, sr_parameter, decorator_obj):
    """
    
    :param gr_parameter: 
    :param sr_parameter: 
    :param decorator_obj: 
    :return: 
    """
    try:
        if 'fields' not in decorator_obj:
            decorator_obj['fields'] = dict()
        gr_field_mapping = dict()
        for field in gr_parameter.get('fields', []):
            gr_field_mapping[field.get('fieldId')] = field
        for field in sr_parameter.get('fields', []):
            if field.get('fieldId') in gr_field_mapping:
                if field.get('fieldType') == "calculated_input":
                    decorator_obj['fields'][field.get('fieldId')] = compare_calculated_obj(field, gr_field_mapping[field.get('fieldId')], decorator_obj['fields'].get(field.get('fieldId'), {}))
                else:
                    if get_value(field) == get_value(gr_field_mapping.get(field.get('fieldId'), {})):
                        decorator_obj.get('fields', {}).get(field.get('fieldId'), {}).pop("source", {})
                    elif field.get('fieldId') in decorator_obj.get('fields', {}):
                        if get_value(gr_field_mapping.get(field.get('fieldId'), {})) != (decorator_obj['fields'][field.get('fieldId')].get('source', {}).get('grValue')):
                            if 'source' not in decorator_obj['fields'][field.get('fieldId')]:
                                decorator_obj['fields'][field.get('fieldId')]['source'] = dict()
                            decorator_obj['fields'][field.get('fieldId')]['source'].update({"grValue": gr_field_mapping.get(field.get('fieldId'), {}).get('value'),
                                                                                            "infoMessage": str(AppConstants.CanvasConstants.vaue_of_attribute_changed_in_gr),
                                                                                            "srValueChange": True})
                    else:
                        if field.get('fieldId') not in decorator_obj['fields']:
                            decorator_obj['fields'][field.get('fieldId')] = dict()
                        decorator_obj['fields'][field.get('fieldId')].update({"source": {"grValue": gr_field_mapping.get(field.get('fieldId'),{}).get('value'),
                        "infoMessage": str(AppConstants.CanvasConstants.vaue_of_attribute_changed_in_gr), "srValueChange": True}})
                if field.get("annotation", "") == gr_field_mapping.get(field.get('fieldId'), {}).get('annotation', ""):
                    if field.get('fieldId') in decorator_obj.get('fields', {}):
                        decorator_obj.get('fields', {}).get(field.get('fieldId'), {}).pop("annotation_source", {})
                elif field.get('fieldId') in decorator_obj.get('fields', {}):
                    if gr_field_mapping.get(field.get('fieldId'), {}).get('annotation', "") != (decorator_obj['fields'][field.get('fieldId')].get('annotation_source', {}).get('grValue')):
                        if 'annotation_source' not in decorator_obj['fields'][field.get('fieldId')]:
                            decorator_obj['fields'][field.get('fieldId')]['annotation_source'] = dict()
                        decorator_obj['fields'][field.get('fieldId')]['annotation_source'].update({"grValue": gr_field_mapping.get(field.get('fieldId'), {}).get('annotation', "")})
                else:
                    if field.get('fieldId') not in decorator_obj['fields']:
                        decorator_obj['fields'][field.get('fieldId')] = dict()
                    decorator_obj['fields'][field.get('fieldId')].update({"annotationS": {"grValue": gr_field_mapping.get(field.get('fieldId'),{}).get('value'),
                    "infoMessage": str(AppConstants.CanvasConstants.vaue_of_attribute_changed_in_gr), "srValueChange": True}})
                if field.get('isQualifier'):

                    if field.get("qualifierValue", "") == gr_field_mapping.get(field.get('fieldId'), {}).get('qualifierValue', ""):
                        decorator_obj.get('fields', {}).get(field.get('fieldId'), {}).pop("qualifier_source", {})
                    elif field.get('fieldId') in decorator_obj.get('fields', {}):
                        if gr_field_mapping.get(field.get('fieldId'), {}).get('qualifierValue') != (decorator_obj['fields'][field.get('fieldId')].get('qualifier_source', {}).get('grValue')):
                            if 'qualifier_source' not in decorator_obj['fields'][field.get('fieldId')]:
                                decorator_obj['fields'][field.get('fieldId')]['qualifier_source'] = dict()
                            decorator_obj['fields'][field.get('fieldId')]['qualifier_source'].update({"grValue": gr_field_mapping.get(field.get('fieldId', {})).get('qualifierValue', ""),
                                                                                                      "infoMessage": str(AppConstants.CanvasConstants.vaue_of_attribute_changed_in_gr),
                                                                                                      "srValueChange": True})
                    else:
                        if field.get('fieldId') not in decorator_obj['fields']:
                            decorator_obj['fields'][field.get('fieldId')] = dict()
                        decorator_obj['fields'][field.get('fieldId')].update({"qualifier_source": {"grValue": gr_field_mapping.get(field.get('fieldId'),{}).get('qualifierValue'),
                        "infoMessage": str(AppConstants.CanvasConstants.vaue_of_attribute_changed_in_gr), "srValueChange": True}})
            elif field.get('fieldId') not in decorator_obj.get('fields', {}):
                decorator_obj['fields'][field.get('fieldId')] = {
                    "source": {"grValue": gr_field_mapping.get(field.get('fieldId'), {}).get('value'),
                               "infoMessage": "This attribute is deleted in General Recipe",
                               "srAttributeChange": True}}


    except Exception as ex:
        logger.error(str(ex))
        logger.error(traceback.format_exc())
        print(traceback.format_exc())


def value_converter(type, value):
    try:
        if type in ["text", "numeric", "text_area", "date"]:
            return value if value else ""
        elif type in ["drop_down", "drop_down_multiselect"]:
            if value in ["", None, []]:
                return None
            return ",".join([item.get('itemName') for item in value])
        elif type == "calculated_input":
            if value.get('tempValue') in ["Error", "NA", "", None]:
                return None
            else:
                return value.get('tempValue', "")
        elif type == "checkbox":
            if value in ["", None, []]:
                return None
            return ",".join(value)
        else:
            if value in ["", None, []]:
                return None
            return value
    except Exception as ex:
        logger.error(str(ex))
        if value in ["", None, []]:
            return None
        return value


def get_value(field_1):
    """

    :param field_1:
    :param field_2:
    :return:
    """

    return value_converter(field_1.get('fieldType'), field_1.get('value', ""))


def compare_calculated_obj(sr_field, gr_field, decorator_obj):
    """

    :param sr_field:
    :param gr_field:
    :param decorator_obj:
    :return:
    """
    try:
        if "calculation_source" not in decorator_obj:
            decorator_obj['calculation_source'] = {"code": dict(),
                                                  "decimalPoints": dict(),
                                                  "roundOff": dict()}
        if sr_field.get('formulaInfo', {}).get('code') == gr_field.get('formulaInfo', {}).get('code'):
            decorator_obj['calculation_source'].pop('code', {})
        elif decorator_obj['calculation_source'].get('code', {}):
            if gr_field.get('formulaInfo', {}).get('code') != decorator_obj['calculation_source'].get('code', {}).get("grValue"):
                decorator_obj['calculation_source']['code'].update(
                    {"grValue": gr_field.get('formulaInfo', {}).get('code'),
                     "srValueChange": True,
                     "infoMessage": str(AppConstants.CanvasConstants.vaue_of_attribute_changed_in_gr)
                     })
        elif gr_field.get('formulaInfo', {}).get('code') != sr_field.get('formulaInfo', {}).get('code'):
            decorator_obj['calculation_source']['code'].update(
                {"grValue": gr_field.get('formulaInfo', {}).get('code'),
                 "srValueChange": True,
                 "infoMessage": str(AppConstants.CanvasConstants.vaue_of_attribute_changed_in_gr)
                 })
        for field in ['roundOff', "decimalPoints", "multiplication_factor"]:
            if sr_field.get('configuration', {}).get(field) == gr_field.get('configuration', {}).get(field):
                decorator_obj['calculation_source'].pop(field, {})
            elif decorator_obj['calculation_source'].get(field, {}):
                if gr_field.get('configuration', {}).get(field) != decorator_obj['calculation_source'].get(field,
                                                                                                         {}).get(
                        "grValue"):
                    decorator_obj['calculation_source'][field].update(
                        {"grValue": gr_field.get('configuration', {}).get(field),
                         "srValueChange": True,
                         "infoMessage": str(AppConstants.CanvasConstants.vaue_of_attribute_changed_in_gr)
                         })
            elif gr_field.get('configuration', {}).get(field) != sr_field.get('configuration', {}).get(field):
                decorator_obj['calculation_source'][field].update(
                    {"grValue": gr_field.get('configuration', {}).get(field),
                     "srValueChange": True,
                     "infoMessage": str(AppConstants.CanvasConstants.vaue_of_attribute_changed_in_gr)
                     })
    except Exception as ex:
        logger.error(str(ex))
        print(traceback.format_exc())
    return decorator_obj


def change_steps_check_in_state(workspace_data, steps_check_in_state_records):
    try:
        for each_step in steps_check_in_state_records:
            step_id = each_step.get("stepId", "")
            workspace_data["recipeObj"][step_id] = each_step.get("stepJson", {})

            if "recipeDecorators" not in workspace_data:
                workspace_data["recipeDecorators"] = {}
            if "steps" not in workspace_data.get("recipeDecorators", {}):
                workspace_data["recipeDecorators"]["steps"] = {}

            if "recipeChangeDecorators" not in workspace_data:
                workspace_data["recipeChangeDecorators"] = {}

            if "steps" not in workspace_data.get("recipeChangeDecorators", {}):
                workspace_data["recipeChangeDecorators"]["steps"] = {}

            workspace_data["recipeChangeDecorators"]["configuration"] = each_step.get("recipeChangeDecorators", {}).get(
                "configuration", {})
            workspace_data["recipeChangeDecorators"]["recipeMetaData"] = each_step.get("recipeChangeDecorators",
                                                                                       {}).get("recipeMetaData", {})
            workspace_data["recipeDecorators"]["steps"][step_id] = each_step.get("recipeDecorators", {}).get(
                "steps", {}).get(step_id, {})
            workspace_data["recipeChangeDecorators"]["steps"][step_id] = each_step.get(
                "recipeChangeDecorators", {}).get("steps", {}).get(step_id, {})
    except Exception as e:
        logger.error(str(e))
    return workspace_data


def clean_step_data(step_data):
    try:
        for each_step in step_data.get("recipeObj", {}):
            no_gr_sampling = False
            no_sr_sampling = False
            for each_activity in step_data.get("recipeObj", {}).get(each_step, {}).get("activityParams", {}):
                if "params" not in step_data.get("recipeObj", {}).get(each_step, {}).get("activityParams", {}).get(
                        each_activity, {}):
                    step_data["recipeObj"][each_step]["activityParams"][each_activity]["params"] = []
                if "equipmentParameters" not in step_data.get("recipeObj", {}).get(each_step, {}).get("activityParams", {}).get(
                        each_activity, {}):
                    step_data["recipeObj"][each_step]["activityParams"][each_activity]["equipmentParameters"] = []

                if not step_data.get("recipeObj", {}).get(each_step, {}).get(
                        "activityParams", {}).get(
                    each_activity, {}).get("materials", {}).get(
                    "materialTemplateTableMetaInfo", {}).get("materialTemplateBodyData"):
                    step_data.get("recipeObj", {}).get(each_step, {}).get(
                        "activityParams", {}).get(
                        each_activity, {}).get("materials", {}).get(
                        "materialTemplateTableMetaInfo", {}).pop("materialTemplateBodyData",None)

                if not step_data.get("recipeObj", {}).get(each_step, {}).get(
                        "activityParams", {}).get(
                        each_activity, {}).get("materials", {}).get(
                    "materialTemplateTableMetaInfo", {}).get("materialTemplateBodyData") and not step_data.get("recipeObj", {}).get(each_step, {}).get(
                        "activityParams", {}).get(
                        each_activity, {}).get("materials", {}).get("selectedTemplate",[]):
                    step_data.get("recipeObj", {}).get(each_step, {}).get("activityParams", {}).get(
                        each_activity, {}).pop("materials", None)

                if not step_data.get("recipeObj", {}).get(each_step, {}).get(
                        "activityParams", {}).get(
                    each_activity, {}).get("srMaterials", {}).get(
                    "materialTemplateTableMetaInfo", {}).get("materialTemplateBodyData"):
                    step_data.get("recipeObj", {}).get(each_step, {}).get(
                        "activityParams", {}).get(
                        each_activity, {}).get("srMaterials", {}).get(
                        "materialTemplateTableMetaInfo", {}).pop("materialTemplateBodyData",None)

                if not step_data.get("recipeObj", {}).get(each_step, {}).get(
                        "activityParams", {}).get(
                        each_activity, {}).get("srMaterials", {}).get(
                    "materialTemplateTableMetaInfo", {}).get("materialTemplateBodyData") and not step_data.get("recipeObj", {}).get(each_step, {}).get(
                        "activityParams", {}).get(
                        each_activity, {}).get("srMaterials", {}).get("selectedTemplate",[]):
                    step_data.get("recipeObj", {}).get(each_step, {}).get("activityParams", {}).get(
                        each_activity, {}).pop("srMaterials", None)

                if "data" not in step_data.get("recipeObj", {}).get(each_step, {}).get("activityParams", {}).get(
                        each_activity, {}).get("sampling", {}) or step_data.get("recipeObj", {}).get(each_step, {}).get("activityParams", {}).get(
                        each_activity, {}).get("sampling", {}).get("data", []) is None:
                    step_data.get("recipeObj", {}).get(each_step, {}).get("activityParams", {}).get(
                        each_activity, {}).pop("sampling", None)
                    no_gr_sampling = True

                if "data" not in step_data.get("recipeObj", {}).get(each_step, {}).get("activityParams", {}).get(
                        each_activity, {}).get("srSampling", {}) or step_data.get("recipeObj", {}).get(each_step, {}).get("activityParams", {}).get(
                        each_activity, {}).get("srSampling", {}).get("data", []) is None:
                    step_data.get("recipeObj", {}).get(each_step, {}).get("activityParams", {}).get(
                        each_activity, {}).pop("srSampling", None)
                    no_sr_sampling = True

            if no_gr_sampling and no_sr_sampling:
                step_data.get("recipeObj", {}).get(each_step, {}).get("activityParams", {}).pop("sampling", None)

    except Exception as e:
        print(traceback.format_exc())
        logger.error(str(e))
    return step_data


def fetch_new_step(workspace_id, step_id, recipe_id, recipe_type, user_id, activity_id, page_no, page_size):

    try:
        response = {}
        updated_ts = ""
        workspace_data= canvas_instance_obj.fetch_unitops_from_workspace_record(workspace_id)
        unitops_data = workspace_data.get("recipeObj", {}).get("defaultData", {}).get("unitops", [])
        for each_step in unitops_data:
            if each_step.get("step_id", "") == step_id:
                updated_ts = each_step.get("updated_ts", "")
        starting_index, ending_index = get_slice_list(int(page_no), int(page_size))
        user_id_encoded = uuid_mngmnt.uuid_encode(user_id)
        updated_ts_encoded = uuid_mngmnt.uuid_encode(updated_ts)
        step_data = canvas_instance_obj.fetch_new_step(workspace_id, recipe_id, step_id, activity_id, starting_index, ending_index,
                                                       user_id_encoded, updated_ts_encoded) or {}
        if recipe_type == "Experiment Recipe":
            step_parameters = fetch_step_parameters(workspace_id, step_id, user_id, activity_id, page_no, page_size,
                                                    "experimental", all_data=False)
        else:
            step_parameters = fetch_step_parameters(workspace_id, step_id, user_id, activity_id, page_no, page_size, "all",
                                          all_data=False)
        if "change_logs" in step_data:
            step_data.update({"recipeObj":{step_id:{}}})
            step_data["recipeObj"][step_id] = step_data.get("change_logs", {}).get(step_id, {}).get(user_id_encoded, {}).get(updated_ts_encoded, {})
        step_data = clean_step_data(step_data)

        if activity_id != "sampling":
            step_data["recipeObj"][step_id]["activityParams"][activity_id]["params"] = \
                step_parameters.get("recipeObj", {}).get(step_id, {}).get("activityParams", {}).get(activity_id, {}).get("params", [])
        # if recipe_type == 'shared':
        #     # check edit access for a recipe
        #     workspace_data = CollaborationManagementAC.new_view_selected_shared_workspace(workspace_id, user_id)


        sampling_data = step_data.get("recipeObj", {}).get(step_id, {}).get("activityParams", {}).get("sampling", {})
        sampling_data.pop('equipmentParameters', None)
        sampling_data.pop('equipParams', None)
        sampling_data.pop('params', None)
        sampling_data.pop('materials', None)
        sampling_data.pop('srMaterials', None)
        steps_check_in_state_records = canvas_instance_obj.fetch_checkin_state_of_steps(recipe_id, user_id)
        step_data = change_steps_check_in_state(step_data, steps_check_in_state_records)
        response["recipeObj"] = step_data.get("recipeObj",{})
        response["is_calc"] = check_for_calc_field_in_step(workspace_id,step_id,user_id)
        response['callback_uri'] = {}
        parameter_uri = "parameters"
        response['callback_uri']["parameter_callback"] = step_parameters.get("parameter_callback", {})
        response['callback_uri']["sr_parameter_callback"] = step_parameters.get("sr_parameter_callback", {})
        response['callback_uri']["eq_class_parameter_callback"] =\
            "/eqClassParameters?workspaceId={}&stepId={}&activityId={}&userId={}".format(workspace_id, step_id, activity_id, user_id)
        response['callback_uri']["eq_parameter_callback"] = \
            "/eqParameters?workspaceId={}&stepId={}&activityId={}&userId={}".format(workspace_id, step_id, activity_id, user_id)
        material_uri = "materials"
        response['callback_uri']["material_callback"] = \
            fetch_callback_details(material_uri, workspace_id, step_id, activity_id, page_no, page_size, step_data.get("materialCount", 0))
        sr_material_uri = "srMaterials"
        response['callback_uri']["sr_material_callback"] = \
            fetch_callback_details(sr_material_uri, workspace_id, step_id, activity_id, page_no, page_size, step_data.get("srMaterialCount", 0))

        sampling_uri = "sampling"
        response['callback_uri']["sampling_callback"] = \
            fetch_callback_details(sampling_uri, workspace_id, step_id, activity_id, page_no, page_size, step_data.get("samplingCount", 0))
        sr_sampling_uri = "srSampling"
        response['callback_uri']["sr_sampling_callback"] = \
            fetch_callback_details(sr_sampling_uri, workspace_id, step_id, activity_id, page_no, page_size, step_data.get("srSamplingCount", 0))
        return response
    except Exception as e:
        print(traceback.format_exc())
        # handling exception
        logger.error(str(e))
        raise Exception(str(e))


def fetch_step_sr_materials(workspace_id, step_id, user_id, activity_id, page_no, page_size, total_count, all_data=False):
    try:
        response = {}
        updated_ts = ""
        workspace_data = canvas_instance_obj.fetch_unitops_from_workspace_record(workspace_id)
        unitops_data = workspace_data.get("recipeObj", {}).get("defaultData", {}).get("unitops", [])
        for each_step in unitops_data:
            if each_step.get("step_id", "") == step_id:
                updated_ts = each_step.get("updated_ts", "")
        user_id_encoded = uuid_mngmnt.uuid_encode(user_id)
        updated_ts_encoded = uuid_mngmnt.uuid_encode(updated_ts)
        starting_index, ending_index = get_slice_list(int(page_no), int(page_size))
        if all_data:
            ending_index = int(total_count - starting_index)
        step_data = canvas_instance_obj.fetch_step_sr_materials(workspace_id, step_id, activity_id, starting_index,
                                                                ending_index, user_id_encoded, updated_ts_encoded)
        if "change_logs" in step_data:
            step_data.update({"recipeObj":{step_id:{}}})
            step_data["recipeObj"][step_id] = step_data.get("change_logs", {}).get(step_id, {}).get(user_id_encoded, {}).get(updated_ts_encoded, {})
        response["recipeObj"] = step_data.get("recipeObj", {})
        parameter_uri = "srMaterials"
        response["sr_material_callback"] = \
            fetch_callback_details(parameter_uri, workspace_id, step_id, activity_id, page_no, page_size,
                                   step_data.get("srMaterialCount", 0))
        if not all_data:
            response["sr_material_callback"]["has_true"] = True
        return response
    except Exception as e:
        # handling exception
        logger.error(str(e))
        raise Exception(str(e))


def fetch_step_materials(workspace_id, step_id, user_id, activity_id, page_no, page_size, total_count, all_data=False):
    try:
        response = {}
        updated_ts = ""
        workspace_data = canvas_instance_obj.fetch_unitops_from_workspace_record(workspace_id)
        unitops_data = workspace_data.get("recipeObj", {}).get("defaultData", {}).get("unitops", [])
        for each_step in unitops_data:
            if each_step.get("step_id", "") == step_id:
                updated_ts = each_step.get("updated_ts", "")
        user_id_encoded = uuid_mngmnt.uuid_encode(user_id)
        updated_ts_encoded = uuid_mngmnt.uuid_encode(updated_ts)
        starting_index, ending_index = get_slice_list(int(page_no), int(page_size))
        if all_data:
            ending_index = int(total_count - starting_index)
        step_data = canvas_instance_obj.fetch_step_materials(workspace_id, step_id, activity_id, starting_index,
                                                             ending_index, user_id_encoded, updated_ts_encoded)
        if "change_logs" in step_data:
            step_data.update({"recipeObj":{step_id:{}}})
            step_data["recipeObj"][step_id] = step_data.get("change_logs", {}).get(step_id, {}).get(user_id_encoded, {}).get(updated_ts_encoded, {})
        response["recipeObj"] = step_data.get("recipeObj", {})
        parameter_uri = "materials"
        response["material_callback"] = \
            fetch_callback_details(parameter_uri, workspace_id, step_id, activity_id, page_no, page_size,
                                   step_data.get("materialCount", 0))
        if not all_data:
            response["material_callback"]["has_more"]= False
        return response
    except Exception as e:
        # handling exception
        logger.error(str(e))
        raise Exception(str(e))


def fetch_step_sr_sampling(workspace_id, step_id, user_id, activity_id, page_no, page_size, total_count, all_data=False):
    try:
        response = {}
        updated_ts = ""
        workspace_data = canvas_instance_obj.fetch_unitops_from_workspace_record(workspace_id)
        unitops_data = workspace_data.get("recipeObj", {}).get("defaultData", {}).get("unitops", [])
        for each_step in unitops_data:
            if each_step.get("step_id", "") == step_id:
                updated_ts = each_step.get("updated_ts", "")
        user_id_encoded = uuid_mngmnt.uuid_encode(user_id)
        updated_ts_encoded = uuid_mngmnt.uuid_encode(updated_ts)
        starting_index, ending_index = get_slice_list(int(page_no), int(page_size))
        if all_data:
            ending_index = int(total_count - starting_index)
        step_data = canvas_instance_obj.fetch_step_sr_sampling(workspace_id, step_id, activity_id,
                                                               starting_index, ending_index, user_id_encoded, updated_ts_encoded)
        if "change_logs" in step_data:
            step_data.update({"recipeObj":{step_id:{}}})
            step_data["recipeObj"][step_id] = step_data.get("change_logs", {}).get(step_id, {}).get(user_id_encoded, {}).get(updated_ts_encoded, {})
        response["recipeObj"] = step_data.get("recipeObj", {})
        parameter_uri = "srSampling"
        response["sr_sampling_callback"] = \
            fetch_callback_details(parameter_uri, workspace_id, step_id, activity_id, page_no, page_size,
                                   step_data.get("srSamplingCount", 0))
        if not all_data:
            response["sr_sampling_callback"]["has_more"] = False
        return response
    except Exception as e:
        # handling exception
        logger.error(str(e))
        raise Exception(str(e))


def fetch_step_sampling(workspace_id, step_id, user_id, activity_id, page_no, page_size, total_count, all_data=False):
    try:
        response = {}
        updated_ts = ""
        workspace_data = canvas_instance_obj.fetch_unitops_from_workspace_record(workspace_id)
        unitops_data = workspace_data.get("recipeObj", {}).get("defaultData", {}).get("unitops", [])
        for each_step in unitops_data:
            if each_step.get("step_id", "") == step_id:
                updated_ts = each_step.get("updated_ts", "")
        user_id_encoded = uuid_mngmnt.uuid_encode(user_id)
        updated_ts_encoded = uuid_mngmnt.uuid_encode(updated_ts)
        starting_index, ending_index = get_slice_list(int(page_no), int(page_size))
        if all_data:
            ending_index = int(total_count - starting_index)
        step_data = canvas_instance_obj.fetch_step_sampling(workspace_id, step_id, activity_id,
                                                            starting_index, ending_index, user_id_encoded, updated_ts_encoded)
        if "change_logs" in step_data:
            step_data.update({"recipeObj":{step_id:{}}})
            step_data["recipeObj"][step_id] = step_data.get("change_logs", {}).get(step_id, {}).get(user_id_encoded, {}).get(updated_ts_encoded, {})
        response["recipeObj"] = step_data.get("recipeObj", {})
        parameter_uri = "sampling"
        response["sampling_callback"] = \
            fetch_callback_details(parameter_uri, workspace_id, step_id, activity_id, page_no, page_size,
                                   step_data.get("samplingCount", 0))
        if not all_data:
            response["sampling_callback"]["has_more"]= False
            
        return response
    except Exception as e:
        # handling exception
        logger.error(str(e))
        raise Exception(str(e))


def fetch_step_eq_class_parameters(workspace_id, step_id, user_id, activity_id, eq_class_id):
    try:
        response = {}
        updated_ts = ""
        workspace_data = canvas_instance_obj.fetch_unitops_from_workspace_record(workspace_id)
        unitops_data = workspace_data.get("recipeObj", {}).get("defaultData", {}).get("unitops", [])
        for each_step in unitops_data:
            if each_step.get("step_id", "") == step_id:
                updated_ts = each_step.get("updated_ts", "")
        user_id_encoded = uuid_mngmnt.uuid_encode(user_id)
        updated_ts_encoded = uuid_mngmnt.uuid_encode(updated_ts)
        step_data, change_log_status = canvas_instance_obj.fetch_step_eq_class_parameters(
            workspace_id, step_id, activity_id, eq_class_id, user_id_encoded, updated_ts_encoded)
        if not change_log_status:
            eq_class_params = step_data.get("recipeObj", {}).get(step_id, {}).get("activityParams", {})\
                .get(activity_id, {}).get("equipParams", [])
            field_mapping = {}
            value = {}
            if len(eq_class_params) > 0:
                parameters = eq_class_params[0].get("params", [])
                for index, each_param in enumerate(parameters):
                    for each_field in each_param.get("fields", []):
                        field_mapping[each_field.get("fieldName", "")] = each_field.get("fieldId", "")
                    for field_name, data in each_param.get("value", {}).items():
                        if field_name in field_mapping:
                            value[field_mapping.get(field_name, "")] = data
                    step_data["recipeObj"][step_id]["activityParams"][activity_id]["equipParams"][0]["params"][index]["value"] = value
            response["recipeObj"] = step_data.get("recipeObj", {})
        else:
            data = step_data.get("change_logs", {}).get(step_id, {}).get(user_id_encoded, {}).get(updated_ts_encoded,
                                                                                                  {})
            eq_class_params = data.get("activityParams", {})\
                .get(activity_id, {}).get("equipParams", [])
            field_mapping = {}
            value = {}
            if len(eq_class_params) > 0:
                parameters = eq_class_params[0].get("params", [])
                for index, each_param in enumerate(parameters):
                    for each_field in each_param.get("fields", []):
                        field_mapping[each_field.get("fieldName", "")] = each_field.get("fieldId", "")
                    for field_name, field_data in each_param.get("value", {}).items():
                        if field_name in field_mapping:
                            value[field_mapping.get(field_name, "")] = field_data
                    data["activityParams"][activity_id]["equipParams"][0]["params"][index]["value"] = value
            response["recipeObj"] = {step_id: data}
        return response
    except Exception as e:
        # handling exception
        logger.error(str(e))
        raise Exception(str(e))


def fetch_step_eq_parameters(workspace_id, step_id, user_id, activity_id, eq_class_id):
    try:
        response = {}
        updated_ts = ""
        workspace_data = canvas_instance_obj.fetch_unitops_from_workspace_record(workspace_id)
        unitops_data = workspace_data.get("recipeObj", {}).get("defaultData", {}).get("unitops", [])
        for each_step in unitops_data:
            if each_step.get("step_id", "") == step_id:
                updated_ts = each_step.get("updated_ts", "")
        user_id_encoded = uuid_mngmnt.uuid_encode(user_id)
        updated_ts_encoded = uuid_mngmnt.uuid_encode(updated_ts)
        step_data, change_log = canvas_instance_obj.fetch_step_eq_parameters(
            workspace_id, step_id, activity_id, eq_class_id, user_id_encoded, updated_ts_encoded)
        if not change_log:
            eq_params = step_data.get("recipeObj", {}).get(step_id, {}).get("activityParams", {})\
                .get(activity_id, {}).get("equipmentParameters", [])
            field_mapping = {}
            value = {}
            if len(eq_params) > 0:
                parameters = eq_params[0].get("params", [])
                for index, each_param in enumerate(parameters):
                    for each_field in each_param.get("fields", []):
                        field_mapping[each_field.get("fieldName", "")] = each_field.get("fieldId", "")
                    for field_name, field_data in each_param.get("value", {}).items():
                        if field_name in field_mapping:
                            value[field_mapping.get(field_name, "")] = field_data
                    step_data["recipeObj"][step_id]["activityParams"][activity_id]["equipmentParameters"][0]["params"][index]["value"] = value
            response["recipeObj"] = step_data.get("recipeObj", {})
        else:
            data = step_data.get("change_logs", {}).get(step_id, {}).get(user_id_encoded, {}).get(updated_ts_encoded,
                                                                                                  {})

            eq_params = data.get("activityParams", {})\
                .get(activity_id, {}).get("equipmentParameters", [])
            field_mapping = {}
            value = {}
            if len(eq_params) > 0:
                parameters = eq_params[0].get("params", [])
                for index, each_param in enumerate(parameters):
                    for each_field in each_param.get("fields", []):
                        field_mapping[each_field.get("fieldName", "")] = each_field.get("fieldId", "")
                    for field_name, field_data in each_param.get("value", {}).items():
                        if field_name in field_mapping:
                            value[field_mapping.get(field_name, "")] = field_data
                    data["activityParams"][activity_id]["equipmentParameters"][0]["params"][index]["value"] = value
            response["recipeObj"] = {step_id: data}
        return response
    except Exception as e:
        # handling exception
        logger.error(str(e))
        raise Exception(str(e))


def fetch_step_parameters(workspace_id, step_id, user_id, activity_id, page_no, page_size, param_type, all_data=False):
    try:
        response = {}
        updated_ts = ""
        workspace_data = canvas_instance_obj.fetch_unitops_from_workspace_record(workspace_id)
        unitops_data = workspace_data.get("recipeObj", {}).get("defaultData", {}).get("unitops", [])
        for each_step in unitops_data:
            if each_step.get("step_id", "") == step_id:
                updated_ts = each_step.get("updated_ts", "")
        user_id_encoded = uuid_mngmnt.uuid_encode(user_id)
        updated_ts_encoded = uuid_mngmnt.uuid_encode(updated_ts)
        if param_type == "experimental":
            starting_index, ending_index = get_slice_list(int(page_no), int(page_size))
            step_data = canvas_instance_obj.fetch_step_er_parameters(workspace_id, step_id, activity_id, starting_index,
                                                                  ending_index, all_data, user_id_encoded,
                                                                  updated_ts_encoded)
            if "change_logs" in step_data:
                step_data.update({"recipeObj": {step_id: {}}})
                step_data["recipeObj"][step_id] = step_data.get("change_logs", {}).get(step_id, {}).get(user_id_encoded,
                                                                                                        {}).get(
                    updated_ts_encoded, {})

            response["recipeObj"] = step_data.get("recipeObj", {})
            parameter_uri = "parameters"
            response["parameter_callback"] = \
                fetch_parameter_callback_details(parameter_uri, workspace_id, step_id, activity_id, "experimental",
                                                 page_no, page_size, step_data.get("paramsCount", 0))
            if all_data:
                response["parameter_callback"]["has_more"] = False
            return response

        sr_count = 0
        gr_count = 0
        param_list = []
        if param_type == "general":
            gr_data, gr_count = canvas_instance_obj.fetch_step_parameters(workspace_id, step_id, activity_id, page_no,
                                                                  page_size, all_data, user_id_encoded, updated_ts_encoded, param_type)
            for each_param in gr_data:
                root_key = "recipeObj"
                change_log = False
                if "change_logs" in each_param:
                    root_key = "change_logs"
                    change_log = True

                if change_log:
                    param_list.append(each_param.get(root_key, {}).get(step_id, {}).get(user_id_encoded, {}).get(
                        updated_ts_encoded, {}).get("activityParams", {}).get(activity_id, {}).get("params", {}))
                else:
                    param_list.append(
                        each_param.get(root_key, {}).get(step_id, {}).get("activityParams", {}).get(activity_id,
                                                                                                    {}).get(
                            "params", []))
        elif param_type == "site":
            sr_data, sr_count = canvas_instance_obj.fetch_step_parameters(workspace_id, step_id, activity_id, page_no,
                                                                  page_size, all_data, user_id_encoded, updated_ts_encoded, param_type)

            for each_param in sr_data:
                root_key = "recipeObj"
                change_log = False
                if "change_logs" in each_param:
                    root_key = "change_logs"
                    change_log = True

                if change_log:
                    param_list.append(each_param.get(root_key, {}).get(step_id, {}).get(user_id_encoded, {}).get(
                        updated_ts_encoded, {}).get("activityParams", {}).get(activity_id, {}).get("params", {}))
                else:

                    param_list.append(
                        each_param.get(root_key, {}).get(step_id, {}).get("activityParams", {}).get(activity_id,
                                                                                                    {}).get(
                            "params", []))
        elif param_type == "all":
            gr_data, gr_count = canvas_instance_obj.fetch_step_parameters(workspace_id, step_id, activity_id, page_no,
                                                                  page_size, all_data, user_id_encoded, updated_ts_encoded, "general")
            sr_data, sr_count = canvas_instance_obj.fetch_step_parameters(workspace_id, step_id, activity_id, page_no,
                                                                  page_size, all_data, user_id_encoded, updated_ts_encoded, "site")


            for each_param in gr_data:
                root_key = "recipeObj"
                change_log = False
                if "change_logs" in each_param:
                    root_key = "change_logs"
                    change_log = True

                if change_log:
                    param_list.append(each_param.get(root_key, {}).get(step_id, {}).get(user_id_encoded, {}).get(
                        updated_ts_encoded, {}).get("activityParams", {}).get(activity_id, {}).get("params", {}))
                else:
                    param_list.append(
                        each_param.get(root_key, {}).get(step_id, {}).get("activityParams", {}).get(activity_id,
                                                                                                    {}).get(
                            "params", []))
            for each_param in sr_data:
                root_key = "recipeObj"
                change_log = False
                if "change_logs" in each_param:
                    root_key = "change_logs"
                    change_log = True

                if change_log:
                    param_list.append(each_param.get(root_key, {}).get(step_id, {}).get(user_id_encoded, {}).get(
                        updated_ts_encoded, {}).get("activityParams", {}).get(activity_id, {}).get("params", {}))
                else:

                    param_list.append(
                        each_param.get(root_key, {}).get(step_id, {}).get("activityParams", {}).get(activity_id, {}).get(
                            "params", []))
        try:
            gr_count = gr_count[0].get("Total")
        except Exception as e:
            gr_count = 0

        try:
            sr_count = sr_count[0].get("Total", 0)
        except Exception as e:
            sr_count = 0
        total_count = gr_count + sr_count

        response = {"recipeObj": {step_id: {"activityParams": {activity_id: {"params": param_list}}}}}
        # response["recipeObj"][step_id]["activityParams"][activity_id]["params"] = []
        # response["recipeObj"][step_id]["activityParams"][activity_id]["params"] = param_list
        parameter_uri = "parameters"
        response["parameter_callback"] = \
            fetch_parameter_callback_details(parameter_uri, workspace_id, step_id, activity_id, "general", page_no, page_size,
                                   gr_count)
        parameter_uri = "parameters"
        response["sr_parameter_callback"] = \
            fetch_parameter_callback_details(parameter_uri, workspace_id, step_id, activity_id, "site", page_no, page_size,
                                   sr_count)
        if all_data and param_type == "general":
            response["parameter_callback"]["has_more"] = False

        if all_data and param_type == "site":
            response["sr_parameter_callback"]["has_more"] = False

        return response
    except Exception as e:
        # handling exception
        logger.error(str(e))
        raise Exception(str(e))


def fetch_step_parameters_temp(workspace_id, step_id, user_id, activity_id, page_no, page_size, all_data=False):
    try:
        response = {}
        updated_ts = ""
        workspace_data = canvas_instance_obj.fetch_unitops_from_workspace_record(workspace_id)
        unitops_data = workspace_data.get("recipeObj", {}).get("defaultData", {}).get("unitops", [])
        for each_step in unitops_data:
            if each_step.get("step_id", "") == step_id:
                updated_ts = each_step.get("updated_ts", "")
        user_id_encoded = uuid_mngmnt.uuid_encode(user_id)
        updated_ts_encoded = uuid_mngmnt.uuid_encode(updated_ts)
        starting_index, ending_index = get_slice_list(int(page_no), int(page_size))
        step_data = canvas_instance_obj.fetch_step_parameters(workspace_id, step_id, activity_id, starting_index,
                                                              ending_index, all_data, user_id_encoded, updated_ts_encoded)
        response["recipeObj"] = step_data.get("recipeObj", {})
        parameter_uri = "parameters"
        response["parameter_callback"] = \
            fetch_callback_details(parameter_uri, workspace_id, step_id, activity_id, page_no, page_size,
                                   step_data.get("paramsCount", 0))
        if not all_data:
            response["parameter_callback"]["has_more"] = False
        return response
    except Exception as e:
        # handling exception
        logger.error(str(e))
        raise Exception(str(e))


def fetch_activity_sr_materials(user_id, activity_id, page_no, page_size, total_count, all_data=False):
    try:
        response = {}
        starting_index, ending_index = get_slice_list(int(page_no), int(page_size))
        if all_data:
            ending_index = int(total_count - starting_index)
        step_data = canvas_instance_obj.fetch_activity_sr_materials(activity_id, starting_index, ending_index)
        response["templateObj"] = step_data.get("templateObj", {})
        parameter_uri = "activitySrMaterials"
        response["sr_material_callback"] = \
            fetch_activity_callback_details(parameter_uri, activity_id, page_no, page_size,
                                            step_data.get("srMaterialCount", 0))
        if not all_data:
            response["sr_material_callback"]["has_more"] = False
        return response
    except Exception as e:
        # handling exception
        logger.error(str(e))
        raise Exception(str(e))


def fetch_activity_materials(user_id, activity_id, page_no, page_size, total_count, all_data=False):
    try:
        response = {}
        starting_index, ending_index = get_slice_list(int(page_no), int(page_size))
        if all_data:
            ending_index = int(total_count - starting_index)
        step_data = canvas_instance_obj.fetch_activity_materials(activity_id, starting_index, ending_index)
        response["templateObj"] = step_data.get("templateObj", {})
        parameter_uri = "activityMaterials"
        response["material_callback"] = \
            fetch_activity_callback_details(parameter_uri, activity_id, page_no, page_size,
                                            step_data.get("materialCount", 0))
        if not all_data:
            response["material_callback"]["has_more"] = False
        return response
    except Exception as e:
        # handling exception
        logger.error(str(e))
        raise Exception(str(e))


def fetch_activity_eq_class_parameters(user_id, activity_id, eq_class_id):
    try:
        response = {}
        step_data = canvas_instance_obj.fetch_activity_eq_class_parameters(
            activity_id, eq_class_id)
        eq_params = step_data.get("templateObj", {}).get(activity_id, {}).get("equipParams", [])
        field_mapping = {}
        value = {}
        if len(eq_params) > 0:
            parameters = eq_params[0].get("params", [])
            for index, each_param in enumerate(parameters):
                for each_field in each_param.get("fields", []):
                    field_mapping[each_field.get("fieldName", "")] = each_field.get("fieldId", "")
                for field_name, data in each_param.get("value", {}).items():
                    if field_name in field_mapping:
                        value[field_mapping.get(field_name, "")] = data
                step_data["templateObj"][activity_id]["equipParams"][0]["params"][index][
                    "value"] = value
        response["templateObj"] = step_data.get("templateObj", {})
        return response
    except Exception as e:
        # handling exception
        logger.error(str(e))
        raise Exception(str(e))


def fetch_activity_eq_parameters(user_id, activity_id, eq_class_id):
    try:
        response = {}
        step_data = canvas_instance_obj.fetch_activity_eq_parameters(
            activity_id, eq_class_id)
        eq_params = step_data.get("templateObj", {}).get(activity_id, {}).get("equipmentParameters", [])
        field_mapping = {}
        value = {}
        if len(eq_params) > 0:
            parameters = eq_params[0].get("params", [])
            for index, each_param in enumerate(parameters):
                for each_field in each_param.get("fields", []):
                    field_mapping[each_field.get("fieldName", "")] = each_field.get("fieldId", "")
                for field_name, data in each_param.get("value", {}).items():
                    if field_name in field_mapping:
                        value[field_mapping.get(field_name, "")] = data
                step_data["templateObj"][activity_id]["equipmentParameters"][0]["params"][index][
                    "value"] = value
        response["templateObj"] = step_data.get("templateObj", {})
        return response
    except Exception as e:
        # handling exception
        logger.error(str(e))
        raise Exception(str(e))


def fetch_activity_level_parameters(activity_id, page_no, page_size, param_type, all_data=False):
    try:
        response = {}
        if param_type == "experimental":
            starting_index, ending_index = get_slice_list(int(page_no), int(page_size))
            step_data = canvas_instance_obj.fetch_level_activity_er_parameters(activity_id, starting_index, ending_index,
                                                                            all_data)
            response["templateObj"] = step_data.get("templateObj", {})
            parameter_uri = "activityParameters"
            response["parameter_callback"] = \
                fetch_activity_parameter_callback_details(parameter_uri, activity_id, "experimental", page_no, page_size,
                                                step_data.get("paramsCount", 0))
            if all_data:
                response["parameter_callback"]["has_more"] = False
            return response
        sr_count = 0
        gr_count = 0
        param_list = []
        if param_type == "general":
            gr_data, gr_count = canvas_instance_obj.fetch_level_activity_parameters(activity_id, page_no,
                                                                  page_size, param_type, all_data)
            for each_param in gr_data:
                param_list.append(
                    each_param.get("templateObj", {}).get(activity_id, {}).get("params", []))
        elif param_type == "site":
            sr_data, sr_count = canvas_instance_obj.fetch_level_activity_parameters(activity_id, page_no,
                                                                  page_size, param_type, all_data)

            for each_param in sr_data:

                param_list.append(
                    each_param.get("templateObj", {}).get(activity_id, {}).get(
                        "params", []))
        elif param_type == "all":
            gr_data, gr_count = canvas_instance_obj.fetch_level_activity_parameters(activity_id, page_no,
                                                                  page_size, "general", all_data)
            sr_data, sr_count = canvas_instance_obj.fetch_level_activity_parameters(activity_id, page_no,
                                                                  page_size, "site", all_data)
            for each_param in gr_data:
                param_list.append(
                    each_param.get("templateObj", {}).get(activity_id, {}).get(
                        "params", []))
            for each_param in sr_data:
                param_list.append(
                    each_param.get("templateObj", {}).get(activity_id, {}).get("params", []))
        try:
            gr_count = gr_count[0].get("Total")
        except Exception as e:
            gr_count = 0

        try:
            sr_count = sr_count[0].get("Total", 0)
        except Exception as e:
            sr_count = 0
        total_count = gr_count + sr_count
        response = {"templateObj": {activity_id: {"params": param_list}}}

        parameter_uri = "activityParameters"
        response["parameter_callback"] = \
            fetch_activity_parameter_callback_details(parameter_uri, activity_id, "general", page_no, page_size,
                                            gr_count)
        response["sr_parameter_callback"] = \
            fetch_activity_parameter_callback_details(parameter_uri, activity_id, "site", page_no, page_size,
                                   sr_count)
        if all_data and param_type == "general":
            response["parameter_callback"]["has_more"] = False

        if all_data and param_type == "site":
            response["sr_parameter_callback"]["has_more"] = False
        return response
    except Exception as e:
        # handling exception
        logger.error(str(e))
        raise Exception(str(e))


def fetch_callback_details(uri, workspace_id, step_id, activity_id, page_no, page_size, count):
    try:
        prev_page_uri = ""
        next_page_uri = ""
        has_more = check_has_more_records(int(page_no), int(page_size), int(count))
        if page_no != 1:
            prev_page_uri = "/{}?workspaceId={}&stepId={}&activityId={}&pageNo={}&pageSize={}&count={}"\
                .format(uri, workspace_id, step_id, activity_id, str(int(page_no)-1), page_size, count)
        if has_more:
            next_page_uri = "/{}?workspaceId={}&stepId={}&activityId={}&pageNo={}&pageSize={}&count={}"\
                .format(uri, workspace_id, step_id, activity_id, str(int(page_no)+1), page_size, count)
        response = {"prev_page_uri": prev_page_uri, "next_page_uri": next_page_uri, "has_more": has_more}
        return response
    except Exception as e:
        # handling exception
        logger.error(str(e))
        raise Exception(str(e))


def fetch_activity_callback_details(uri, activity_id, page_no, page_size, count):
    try:
        prev_page_uri = ""
        next_page_uri = ""
        has_more = check_has_more_records(int(page_no), int(page_size), int(count))
        if page_no != 1:
            prev_page_uri = "/{}?activityId={}&pageNo={}&pageSize={}&count={}"\
                .format(uri, activity_id, str(int(page_no)-1), page_size, count)
        if has_more:
            next_page_uri = "/{}?activityId={}&pageNo={}&pageSize={}&count={}"\
                .format(uri, activity_id, str(int(page_no)+1), page_size, count)
        response = {"prev_page_uri": prev_page_uri, "next_page_uri": next_page_uri, "has_more": has_more}
        return response
    except Exception as e:
        # handling exception
        logger.error(str(e))
        raise Exception(str(e))


def fetch_activity_parameter_callback_details(uri, activity_id, parameter_type, page_no, page_size, count):
    try:
        prev_page_uri = ""
        next_page_uri = ""
        has_more = check_has_more_records(int(page_no), int(page_size), int(count))
        if page_no != 1:
            prev_page_uri = "/{}?activityId={}&param_type={}&pageNo={}&pageSize={}&count={}"\
                .format(uri, activity_id, parameter_type, str(int(page_no)-1), page_size, count)
        if has_more:
            next_page_uri = "/{}?activityId={}&param_type={}&pageNo={}&pageSize={}&count={}"\
                .format(uri, activity_id, parameter_type, str(int(page_no)+1), page_size, count)
        response = {"prev_page_uri": prev_page_uri, "next_page_uri": next_page_uri, "has_more": has_more}
        return response
    except Exception as e:
        # handling exception
        logger.error(str(e))
        raise Exception(str(e))


def fetch_parameter_callback_details(uri, workspace_id, step_id, activity_id, param_type, page_no, page_size, count):
    try:
        prev_page_uri = ""
        next_page_uri = ""
        has_more = check_has_more_records(int(page_no), int(page_size), int(count))
        if page_no != 1:
            prev_page_uri = "/{}?workspaceId={}&stepId={}&activityId={}&param_type={}&pageNo={}&pageSize={}&count={}"\
                .format(uri, workspace_id, step_id, activity_id, param_type, str(int(page_no)-1), page_size, count)
        if has_more:
            next_page_uri = "/{}?workspaceId={}&stepId={}&activityId={}&param_type={}&pageNo={}&pageSize={}&count={}"\
                .format(uri, workspace_id, step_id, activity_id, param_type, str(int(page_no)+1), page_size, count)
        response = {"prev_page_uri": prev_page_uri, "next_page_uri": next_page_uri, "has_more": has_more}
        return response
    except Exception as e:
        # handling exception
        logger.error(str(e))
        raise Exception(str(e))


def get_slice_list(page_no=1, page_size=20):
    try:
        index_one = (page_no * page_size) - page_size
        index_two =  page_size
        return index_one, index_two
    except Exception as e:
        # handling exception
        logger.error(str(e))
        raise Exception(str(e))


def merge_patch_differences(patch_obj, old_workspace_data):
    try:
        latest_workspace_data = jsonpatch.apply_patch(old_workspace_data, patch_obj)
        return latest_workspace_data
    except Exception as e:
        logger.error(str(e))
        raise Exception(str(e))


def generate_summary(input_json):
    try:
        ignored_keys = ['sampling', 'equipment_class_summary', 'equipments_summary', 'solution_class_summary']
        updated_timestamp = ""
        workspace_record = \
            canvas_instance_obj.fetch_record_object_from_workspace_record(
                input_json.get('workspaceId', ''))
        for each_step in workspace_record.get("recipeObj", {}).get("defaultData", {}).get("unitops", []):
            if each_step.get("step_id", "") == input_json.get("stepId", ""):
                updated_timestamp = each_step.get("updated_ts", "")

        process_type = workspace_record.get("processType", "") or ""
        uuid_user_id = uuid_mngmnt.uuid_encode(input_json.get("userId", ""))
        uuid_update_ts = uuid_mngmnt.uuid_encode(updated_timestamp)
        change_log_unitop_data = workspace_record.get("change_logs", {}).get(input_json.get('stepId', ''), {}).get(
            uuid_user_id, {}).get(uuid_update_ts, {})
        step_data = change_log_unitop_data or workspace_record.get('recipeObj', {}).get(
            input_json.get('stepId', ''), {})
        request_type = input_json.get('type', "")
        step_data = merge_patch_differences(input_json.get('patch', []), step_data)

        if request_type == "material_summary":
            data_key = "solutions"
            material_key = "materials"

            if process_type.lower() == "site":
                material_key = "srMaterials"

            temp_id_list = {}
            equip_params = {"headerData": [
                {
                    "key": "materialID",
                    "label": "Material ID"
                },
                {
                    "key": "material_name",
                    "label": "Material Name"
                },
                {
                    "key": "quantity",
                    "label": "Quantity"
                },
                {
                    "key": "quantity_units",
                    "label": "Quantity Units"
                },
                {
                    "key": "prepareSeparately",
                    "label": "Prepare Separately"
                },
                {
                    "key": "activity_name",
                    "label": "Activity"
                }
            ], "bodyData": []}
            for activity in step_data.get('activities', []):
                if activity.get('component_key') not in ignored_keys and material_key in step_data.get('activityParams',{}).get(activity['component_key'],{}):
                    try:
                        quantity_type = ""
                        for field in step_data.get('activityParams',{}).get(activity['component_key']).get(material_key,{}) \
                                .get('materialTemplateTableMetaInfo',{}).get('materialTemplateFields',{}):
                            if field.get('attributeKey') == "quantity":
                                quantity_type =field.get("attributeType","")

                        for param in step_data.get('activityParams',{}).get(activity['component_key']).get(material_key,{}) \
                                .get('materialTemplateTableMetaInfo',{}).get('materialTemplateBodyData',{}):

                                prp = param.get('prepareSeparately', '')

                                units = 'NA' if len(param.get('quantity_units', [])) < 1 else param['quantity_units'][0].get('itemName')
                                val_type = ""
                                val =0
                                try:
                                    if quantity_type == "calculated_input":
                                        val = float(param.get('quantity',{}).get('value'))
                                        val_type = "num"
                                    elif quantity_type in ["drop_down","drop_down_multiselect"]:
                                        quantity_list =list()
                                        for each_element in param.get("quantity",[]):
                                            quantity_list.append(each_element.get("itemName",""))
                                            val = ",".join(quantity_list)
                                    elif quantity_type in ["text"]:
                                        val = param.get("quantity","")
                                        val_type = ""
                                    else:
                                        val = float(param.get('quantity')) if type(param.get('quantity')) != str else param.get("quantity","")
                                        val_type = "num" if type(param.get('quantity')) != str else ""
                                except:
                                    val = param.get('quantity', {'value':''}).get('value', '')
                                    val_type = ""

                                key = param['materialID'] + "$^$" + param['material_name'] + "$^$" + val_type + "$^$" + units + "$^$" + str(prp)

                                if val_type == "num" and key in temp_id_list:
                                    temp_id_list[key]['quantity'] = temp_id_list[key]['quantity'] + val
                                else:
                                    temp_id_list.update({key: {"materialID": param['materialID'],
                                                               "material_name": param['material_name'],
                                                               "quantity": val,
                                                               "quantity_units": units,
                                                               "activity_name": activity['label'],
                                                               "prepareSeparately": prp}})
                    except:
                        pass

            equip_params['bodyData'] = list(temp_id_list.values())

        else:
            temp_id_list = []
            equip_params = []
            if request_type == "equipment_class_summary":
                id_key = "equipmentClassId"
                params_key = "equipParams"
                data_key = "equipParams"
            elif request_type == "equipments_summary":
                id_key = "equipmentId"
                params_key = "equipmentParameters"
                data_key = "equipments"
            else:
                raise Exception("Unknown request type specified")
            for activity in step_data['activities']:
                if activity['component_key'] not in ignored_keys and params_key in \
                        step_data['activityParams'][activity['component_key']]:
                    for param in step_data['activityParams'][activity['component_key']][params_key]:
                        eq_id = param[id_key]
                        if eq_id not in temp_id_list:
                            temp_id_list.append(eq_id)
                            equip_params.append(param)
                        else:
                            for each_equip_param in equip_params:
                                if eq_id == each_equip_param.get(id_key):
                                    for new_param,existing_param in itertools.zip_longest(param.get("params",[]),each_equip_param.get("params",[]),fillvalue=-1):
                                        new_fields = new_param.get("fields",[])
                                        old_fields = existing_param.get("fields",[])
                                        old_fields_copy = copy.deepcopy(old_fields)
                                        new_field_mapping =dict()
                                        old_field_mapping =dict()
                                        for new_field in new_fields:
                                            new_field_mapping.update({new_field.get("fieldId"):new_field})
                                        for old_field in old_fields_copy:
                                            old_field_mapping.update({old_field.get("fieldId"): old_field})
                                        for field in new_field_mapping:
                                            if field in old_field_mapping and old_field_mapping.get(field,{}).get("value") != new_field_mapping.get(field,{}).get("value"):
                                                old_fields.append(new_field_mapping.get(field,{}))


        return {data_key: equip_params, "non_editable": False, "data": [], "id": request_type}

    except Exception as e:
        logger.error(traceback.format_exc())
        logger.error(str(e))
        raise Exception(str(e))


def step_data_duplication_check(input_json):
    try:
        workspace_id = input_json.get("workspaceId","")
        step_id = input_json.get("stepId","")
        activity_id = input_json.get("activityId","")
        user_id = input_json.get("userId","")
        element_type = input_json.get("type","")
        element_id_list = input_json.get("id","")
        updated_ts = ""
        workspace_data = canvas_instance_obj.fetch_unitops_from_workspace_record(workspace_id)
        unitops_data = workspace_data.get("recipeObj", {}).get("defaultData", {}).get("unitops", [])
        for each_step in unitops_data:
            if each_step.get("step_id", "") == step_id:
                updated_ts = each_step.get("updated_ts", "")
        user_id_encoded = uuid_mngmnt.uuid_encode(user_id)
        updated_ts_encoded = uuid_mngmnt.uuid_encode(updated_ts)
        duplication_status = canvas_instance_obj.step_data_duplication_check(
            workspace_id, step_id, activity_id, element_id_list,element_type, user_id_encoded, updated_ts_encoded)
        return duplication_status
    except Exception as e:
        # handling exception
        logger.error(str(e))
        raise Exception(str(e))

def check_for_calc_field_in_step(workspace_id,step_id,user_id):
    try:
        is_calc_enabled = False
        step_data = canvas_instance_obj.fetch_step_data_from_workspace(workspace_id,step_id)
        str_step_data = str(step_data)
        if "calculated_input" in str_step_data:
            is_calc_enabled = True
        return is_calc_enabled
    except Exception as e:
        # handling exception
        logger.error(str(e))
        raise Exception(str(e))


def fetch_disparity_check_details(input_json):
    """

    :param template_data:
    :return:
    """
    response = dict()
    disparity_check = get_canvas_static_jsons("canvas_disparity_check")
    # print(disparity_check)
    response = disparity_check

    try:
        recipe_id = input_json.get('recipeId', '')
        # workspace_id = input_json.get('workspacaId', '')
        # user_id = input_json.get('userId', '')
        # print("recipe_id", recipe_id)

        #Fetching all config objects associated with this recipe
        config_to_recipe_data = canvas_instance_obj.fetch_config_object_to_recipe(recipe_id)
        # print("config_to_recipe_data", config_to_recipe_data)

        body_content = list()
        if config_to_recipe_data:
            for each_data in config_to_recipe_data:
                # print("each_data", each_data)
                config_object_data = dict()
                object_type = each_data.get('objectType', '')
                object_type_map = AppConstants.AuditLogsConstants.object_type_mapping.get(object_type, '')
                config_object_version_id = each_data.get('configObjectVersionId', '')
                config_object_data['configObjType'] = object_type_map
                config_object_data['currentVersionRecipe'] = each_data.get('configObjectVersion', '')
                config_object_data['stepId'] = each_data.get('stepId', '')
                config_object_id = each_data.get('configObjectId', '')
                config_object_data["positionDetails"] = each_data.get('canvasPath', '')

                # Getting the version collection name, version collection ref key according to object type
                version_collection_data = AppConstants.ConfigObjectConstant.config_object_to_collection_mapping.get(
                    object_type, {})
                # print("version_collection_data", version_collection_data)
                version_ref_key = version_collection_data.get('version_ref', '')
                collection_name = version_collection_data.get('version_collection', '')

                # Latest Approved version
                config_obj_coll_data = canvas_instance_obj.fetch_config_object_version_collection_data(
                    version_ref_key, config_object_id, collection_name)
                # print("config_obj_coll_data", config_obj_coll_data)

                if config_obj_coll_data:
                    config_object_data['currentVersionConfiguration'] = config_obj_coll_data.get('version', '')
                    config_object_data['dateTime'] = config_obj_coll_data.get('modifiedTs')
                else:
                    config_object_data['currentVersionConfiguration'] = ""
                    config_object_data['dateTime'] = ""

                # Config object curent version object name fetching
                config_obj_rec_cur_data = canvas_instance_obj.fetch_record_by_id(collection_name,
                                                                                 config_object_version_id)
                # print("config_obj_rec_cur_data", config_obj_rec_cur_data)

                # Fetching the object type to modality mapping json
                config_obj_name_ref = AppConstants.ConfigObjectConstant.config_obj_id_to_name.get(object_type, '')
                # print("config_obj_name_ref", config_obj_name_ref)
                config_object_data['configObjName'] = config_obj_rec_cur_data.get(config_obj_name_ref, "")

                #TODO: Change Reason
                # config_object_data['changes'] = ""

                # latest_version_data = canvas_instance_obj.f
                # print("config_obj_coll_data", config_obj_coll_data)

                # Adding the record to response only if mismatch in version
                if config_object_data['currentVersionConfiguration'] != config_object_data['currentVersionRecipe']:
                    if config_object_data not in body_content:
                        # print("config_object_data", config_object_data)
                        body_content.append(config_object_data)
        print("body_content", body_content)
        response['bodyContent'] = body_content
        return response
    except Exception as ex:
        print(str(ex))
        logger.error(str(ex))
        logger.error(traceback.format_exc())
        return response
