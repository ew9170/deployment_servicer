from kubernetes import client, config, watch
import argparse
import yaml
import logging
import threading

from kubernetes.client import ApiException

# TODO: Figure out and delete
"""
1.) How will the user of this controller specify what deployments to watch with this controller?
    
    In all likelihood, the user will create this pod/deployment by specifying the image in a YAML.
    How will this program determine what the user wants?
    Possibilities include:
        - command line arguments (argparse)
        - somehow using command line arguments through the YAML used to create the controller (maybe with a default?)
        
2.) How will the user create the controller?
    Possibilities include:
        - YAML: kubectl apply -f "servicer.yaml"
        - kubectl run [name] --image="eatonwu/deployment_servicer"
        
3.) How do we determine whether a deployment should be serviced?
    
    The deployment should have an label that we read.
    By default, it should be "serviced: True"
    This pair should probably be in the metadata.labels section.
    
4.) How do we communicate this to users?
    
    ReadMe.md in our directory
    
5.) Labels vs Annotations

    Labels are better for this vs annotations, as labels allow users to filter deployments with certain labels
    using certain API calls such as LIST and WATCH.
    
6.) Other Considerations:

    - We aren't checking for maximum name/label length (labels = max 63 chars, names = max 253 chars)
    - 
        
7.) selectors and names in the deployment vs the selectors and names in the service

    This one is pretty confusing; the deployment needs to determine and specify which pods that the deployment controls,
    which Kubernetes does by using match_labels and match_expressions. On the service end, the selector uses the
    pod_template selector (all the pods created by the deployment via its replicaset will have the same labels).
    Fortunately for us, Services currently only support match_labels, so we don't need to worry about match_expressions.
     
"""


def is_serviced_deployment(event: dict, label=None):
    """
    Helper function to determine if the event was triggered on a marked deployment
    The marker should be in .metadata.labels

    :param label: the key of the annotation that we want to check for
    :param event: event passed by the watch
    :return: bool; true if deployment has a flag
    """
    if label is None:
        label = "serviced"
    return label in event['object'].metadata.labels


def update_service(event: dict):
    """
    This function reads a modification on a deployment and attempts to update its corresponding service.
    For us, the only state worthy of note is the deployment's ports, because users have the ability to add ports to
    a deployment retroactively (metadata.name is immutable)

    Unfortunately there doesn't seem to be functionality to implicitly delete ports in the Python Kubernetes-client.
    kubectl makes use of three-way diff logic to accomplish what it does with kubectl apply -f.
    I'm not going to bother with that.
    :param event: a modification event created by a Kubernetes watch
    :return:
    """
    deployment = event['object']
    deployment_name = deployment.metadata.name
    k8s_core_api = client.CoreV1Api()
    service_name = deployment_name + '-service'
    ports_dict = {}
    ports_list = []

    for container in event['object'].spec.template.spec.containers:
        if container.ports is not None:
            for port in container.ports:
                ports_dict[port.container_port] = port.protocol

    # given the ports and protocols we've seen in the containers, create a V1ServicePort object for each
    for port_obj in ports_dict:
        ports_list.append(client.V1ServicePort(
            name=str(port_obj) + '-' + str(ports_dict[port_obj]).lower(),
            port=port_obj,
            protocol=ports_dict[port_obj],  # retrieves the protocol of the port
        ))

    patch_body = {'spec': {'ports': ports_list}}
    k8s_core_api.patch_namespaced_service(body=patch_body, name=service_name, namespace=deployment.metadata.namespace)
    logging.info(f'patch to \'{service_name}\' applied')


def create_service_from_deployment(deployment: client.V1Deployment):
    deployment_name = deployment.metadata.name
    service_name = deployment_name + '-service'
    k8s_core_api = client.CoreV1Api()
    ports_dict = {}
    ports_list = []

    # for each container in this pod, look at the exposed ports and add them to a list
    for container in deployment.spec.template.spec.containers:
        if container.ports is not None:
            for port in container.ports:
                ports_dict[port.container_port] = port.protocol

    # given the ports and protocols we've seen in the containers, create a V1ServicePort object for each
    for port_obj in ports_dict:
        ports_list.append(client.V1ServicePort(
            name=str(port_obj) + '-' + str(ports_dict[port_obj]).lower(),
            port=port_obj,
            protocol=ports_dict[port_obj],  # retrieves the protocol of the port
        ))

    temp_body = client.V1Service(
        api_version="v1",
        kind="Service",
        metadata=client.V1ObjectMeta(
            name=service_name
        ),
        spec=client.V1ServiceSpec(
            selector=deployment.spec.template.metadata.labels,
            ports=ports_list
        )
    )
    k8s_core_api.create_namespaced_service(namespace=deployment.metadata.namespace, body=temp_body)
    logging.info("service created")


def create_service_with_event(event: dict):
    """
    Creates a service with a deployment event;
    This function works on deployments with pods that have multiple containers.

    When creating a service manually (with a YAML document), we should specify:
        - .metadata.name ('deploymentname_service')
        - .spec.selector.'name' ('deployment-selector_name')
        - .spec.ports[].protocol (TCP/UDP)
        - .spec.ports[].port
        - .spec.ports[].targetPort

    :param event:
    :return:
    """
    deployment = event['object']
    create_service_from_deployment(deployment)


def delete_service_with_event(event: dict):
    k8s_core = client.CoreV1Api()
    k8s_core.delete_namespaced_service(name=event['object'].metadata.name + '-service',
                                       namespace=event['object'].metadata.namespace)
    logging.info("service deleted")


def does_service_for_deployment_exist(deployment):
    k8s_core = client.CoreV1Api()
    # get deployment name, add -service, and select for it using API call
    service_name = deployment.metadata.name + '-service'
    service_list = k8s_core.list_service_for_all_namespaces(field_selector='metadata.name=' + service_name)
    return len(service_list.items) > 0


def watch_deployments(args=None):
    """
    This function creates a Kubernetes watch object on deployments on the cluster this pod presumably runs on.
    :param args: a namespace object created by argparse; contains flag 'f', which is the name of the label
    :return: None
    """
    if args is not None:
        label = args.f
    else:
        label = None
    config.load_kube_config()
    k8s_apps_v1 = client.AppsV1Api()
    w = watch.Watch()
    logging.info('Deployment watch started')
    for event in w.stream(k8s_apps_v1.list_deployment_for_all_namespaces):
        deployment = event['object']
        if event['type'] == 'ADDED':
            if is_serviced_deployment(event, label):
                try:
                    create_service_with_event(event)
                except ApiException as e:
                    logging.warning(f"Creation of service for deployment \'{deployment.metadata.name}\' failed; "
                                    f"may already exist")
                    continue
        if event['type'] == 'DELETED':
            if is_serviced_deployment(event, label):
                try:
                    delete_service_with_event(event)
                except ApiException as e:
                    logging.warning(f"Deletion of service of deployment \'{deployment.metadata.name}\' failed; "
                                    f"doesn't exist")
                    continue
        if event['type'] == 'MODIFIED':
            try:
                if (does_service_for_deployment_exist(deployment)) and not is_serviced_deployment(event, label):
                    delete_service_with_event(event)
                    logging.info(f"Object modified; service for deployment \'{deployment.metadata.name}\' deleted")
                elif (not does_service_for_deployment_exist(deployment)) and is_serviced_deployment(event, label):
                    create_service_with_event(event)
                    logging.info(f"Object modified; service for deployment \'{deployment.metadata.name}\' created")
                elif does_service_for_deployment_exist(deployment) and is_serviced_deployment(event, label):
                    update_service(event)
                    logging.info(f"Object modified; service for deployment \'{deployment.metadata.name}\' modified")
            except ApiException as e:
                temp = yaml.safe_load(e.body)
                logging.warning(f"Modification on deployment "
                                f"\'{deployment.metadata.name}\' failed because {temp['message']}")
                continue


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument('-f', type=str, metavar='FLAG',
                        help='Optionally change the annotation used to flag deployments for servicing.'
                             ' In the YAML, the key of the annotation match the specified annotation.'
                             ' By default, the annotation is \'serviced\'')
    parser.add_argument('--port', type=int, help='The desired port of service, argument defaults to deployment port')
    parser.add_argument('--target_port', type=int,
                        help='The desired target port (the port to forward to), argument defaults to deployment port')
    parser.add_argument('-v', '--verbose', type=int, help='Used to specify the verbosity for logging.'
                                                          ' Scales from 0-4:'
                                                          '\n0: DEBUG'
                                                          '\n1: INFO'
                                                          '\n2: WARNING (default)'
                                                          '\n3: ERROR'
                                                          '\n4: CRITICAL')

    return parser.parse_args()


def get_deployment_name_from_service_name(service_name):
    if len(service_name) > 8:
        return service_name[:-8]
    else:
        return ''


def get_deployments_from_name(deployment_name: str):
    config.load_kube_config()
    k8s_core = client.AppsV1Api()
    # get deployment name, add -service, and select for it using API call
    deployment_list = k8s_core.list_deployment_for_all_namespaces(field_selector='metadata.name=' + deployment_name)
    return deployment_list


def does_deployment_exist(deployment_name: str):
    config.load_kube_config()
    k8s_core = client.AppsV1Api()
    # get deployment name, add -service, and select for it using API call
    deployment_list = k8s_core.list_deployment_for_all_namespaces(field_selector='metadata.name=' + deployment_name)
    return len(deployment_list.items) > 0


def watch_services(args=None):
    """
    This function creates a watch for deleted services and recreates them if a deployment with a corresponding
    name and servicing label exists. This function is meant to run concurrently with watch_deployments().
    :return: None
    """
    if args is not None:
        label = args.f
    else:
        label = None
    config.load_kube_config()
    k8s_core = client.CoreV1Api()
    w = watch.Watch()
    logging.info('Service watch started')
    for event in w.stream(k8s_core.list_service_for_all_namespaces):
        service = event['object']
        if event['type'] == 'DELETED':
            logging.info('Service deleted')
            potential_deploy_name = get_deployment_name_from_service_name(service.metadata.name)
            if does_deployment_exist(potential_deploy_name):
                recreate_service_with_event(event)
                logging.info("Service recreated")


def recreate_service_with_event(event: dict):
    """
    Given a service deletion event, this function gets the associated deployment (if it exists),
    and recreates the service. The reason why we don't just call create_namespaced_service()
    is because it's possible that the deployment could be out of date.
    :param event: a service deletion event
    :return: None
    """
    service_name = event['object'].metadata.name
    deploy_name = get_deployment_name_from_service_name(service_name)
    deployment_list = get_deployments_from_name(deploy_name)
    if len(deployment_list.items) > 0:
        deployment = deployment_list.items[0]
        create_service_from_deployment(deployment)


def configure_logging(args):
    settings = {0: 'DEBUG', 1: 'INFO', 2: 'WARNING', 3: 'ERROR', 4: 'CRITICAL'}

    if args.verbose is not None and args.verbose in settings:
        logging.basicConfig(format='%(levelname)s: %(message)s', level=settings[args.verbose])
    else:
        logging.basicConfig(format='%(levelname)s: %(message)s', level=settings[2])

    logging.info("Logging configured")


# the goal is to watch for new deployments (with a specific flag)
# see if deployment has an associated service
# if so, watch for if the service(s) get destroyed, if so, create a new service
# if not, create a new service
def main():
    """
    This controller will run on a Pod within a Kubernetes cluster.
    This controller tracks Deployments, and will create a Service object that has:
        - the same selector
    """
    args = parse_args()
    configure_logging(args)
    watch_deployments_thread = threading.Thread(target=watch_deployments, args=(args,), daemon=True)
    watch_services_thread = threading.Thread(target=watch_services, args=(args,), daemon=True)
    watch_deployments_thread.start()
    watch_services_thread.start()
    watch_deployments_thread.join()
    watch_services_thread.join()


if __name__ == "__main__":
    main()
