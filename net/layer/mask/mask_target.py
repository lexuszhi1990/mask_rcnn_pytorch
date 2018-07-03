import copy
import torch
import numpy as np
from net.utils.func_utils import to_tensor
from net.utils.box_utils import is_small_box, resize_instance
from net.lib.box_overlap.cython_box_overlap import cython_box_overlap
from net.layer.rcnn.rcnn_target import add_truth_box_to_proposal


def make_one_mask_target(cfg, image, proposals, truth_box, truth_label, truth_instance):
    """
    make mask targets for one image.
    1. assign truth box to each rpn_proposals by threshold for fg/bg
    2. crop assigned instance into bbox size
    3. resize to maskhead's_train output size.
    :param image: image as (H, W, C) numpy array
    :param proposals: list of regional rpn_proposals generated by RCNN. e.g.
        [[i, x0, y0, x1, y1, score, label], ...]
    :param truth_box: list of truth boxes. e.g.
        [[x0, y0, x1, y1], ...]
    :param truth_label: 1s
        maskhead are used to predict mask,
        all masks are positive rpn_proposals. (foreground)
        here we have 2 classes so it's_train fixed to 1
    :param truth_instance: list of truth instances, (H, W)
    :return:
        sampled_proposal: same as rpn_proposals
        sampled_label: same as truth_label
        sampled_instance: cropped instance, matching maskhead's_train output
        sampled_assign: index of truth_box each rpn_proposals belongs to
    """
    sampled_proposal = torch.FloatTensor(0, 7).to(cfg.device)
    sampled_label    = torch.LongTensor (0, 1).to(cfg.device)
    sampled_instance = torch.FloatTensor(0, 1, 1).to(cfg.device)

    # filter invalid rpn_proposals like small rpn_proposals
    _, height, width = image.size()
    num_proposal = len(proposals)

    valid = []
    for i in range(num_proposal):
        box = proposals[i, 1:5]
        if not(is_small_box(box, min_size=cfg.mask_train_min_size)):  # is_small_box_at_boundary
            valid.append(i)
    proposals = proposals[valid]

    num_proposal = len(proposals)
    if len(truth_box) > 0 and num_proposal > 0:
        # assign bbox to rpn_proposals by overlap threshold
        box = proposals[:, 1:5]
        # for each bbox, the index of gt which has max overlap with it
        overlap = cython_box_overlap(box, truth_box)
        argmax_overlap = np.argmax(overlap, 1)
        max_overlap = overlap[np.arange(num_proposal), argmax_overlap]

        fg_index = np.where(max_overlap >= cfg.mask_train_fg_thresh_low)[0]

        if len(fg_index) > 0:
            fg_length = len(fg_index)
            num_fg = cfg.mask_train_batch_size
            fg_index = fg_index[
                np.random.choice(fg_length, size=num_fg, replace=fg_length < num_fg)
            ]

            sampled_proposal = proposals[fg_index]
            sampled_assign   = argmax_overlap[fg_index]     # assign a gt to each bbox
            sampled_label    = truth_label[sampled_assign]  # assign gt's_train label to each bbox
            sampled_instance = []
            for i in range(len(fg_index)):
                instance = truth_instance[sampled_assign[i]]  # for each positive bbox, find instance it belongs to
                box  = sampled_proposal[i, 1:5]
                crop = resize_instance(instance, box, cfg.mask_size)  # crop the instance by box
                sampled_instance.append(crop[np.newaxis, :, :])

        # save
        sampled_instance = np.vstack(sampled_instance) if len(sampled_instance) > 0 else np.zeros((0, cfg.mask_size, cfg.mask_size))

        sampled_proposal = to_tensor(sampled_proposal, cfg.device)
        sampled_label    = to_tensor(sampled_label, cfg.device)
        sampled_instance = to_tensor(sampled_instance, cfg.device)

    return sampled_proposal, sampled_label, sampled_instance


def make_mask_target(cfg, images, rcnn_proposals, truth_boxes, truth_labels, truth_instances):
    """
    :param:
        images:
        rcnn_proposals:
        truth_boxes:
        truth_labels:
        truth_instances:
    :return:
        sampled_proposals: bbox which has overlap with one gt > threshold
        sampled_labels: class label of the bbox
        sampled_assigns: which gt the bbox is assigned to (seems not used)
        sampled_instances: cropped/resized instance mask into mask output (28*28)
    """
    rcnn_proposals_np = rcnn_proposals.detach().cpu().numpy()

    truth_boxes     = copy.deepcopy(truth_boxes)
    truth_labels    = copy.deepcopy(truth_labels)
    truth_instances = copy.deepcopy(truth_instances)
    batch_size = len(images)
    for b in range(batch_size):
        index = np.where(truth_labels[b]>0)[0]
        truth_boxes [b] = truth_boxes [b][index]
        truth_labels[b] = truth_labels[b][index]
        truth_instances[b] = truth_instances[b][index]

    sampled_proposals  = []
    sampled_labels     = []
    sampled_instances  = []

    batch_size = len(images)
    for b in range(batch_size):
        image          = images[b]
        truth_box      = truth_boxes[b]
        truth_label    = truth_labels[b]
        truth_instance = truth_instances[b]

        if len(truth_box) != 0:
            if len(rcnn_proposals_np) == 0:
                proposal = np.zeros((0, 7), np.float32)
            else:
                proposal = rcnn_proposals_np[rcnn_proposals_np[:, 0] == b]

            #proposal = add_truth_box_to_proposal(proposal, b, truth_box, truth_label)

            sampled_proposal, sampled_label, sampled_instance = \
                make_one_mask_target(cfg, image, proposal, truth_box, truth_label, truth_instance)

            sampled_proposals.append(sampled_proposal)
            sampled_labels.append(sampled_label)
            sampled_instances.append(sampled_instance)

    sampled_proposals = torch.cat(sampled_proposals,0)
    sampled_labels    = torch.cat(sampled_labels,0)
    sampled_instances = torch.cat(sampled_instances,0)

    return sampled_proposals, sampled_labels, sampled_instances